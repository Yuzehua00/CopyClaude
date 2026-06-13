from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from copy_claude.core.config import CopyClaudeConfig

# 格式定义
_TEXT_FMT = 'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"'
_JSON_FMT = '{"level":"%(levelname)s","ts":"%(asctime)s","source":"%(name)s","msg":"%(message)s"}'

# 根据配置初始化 root logger：设置级别、格式，并挂载 stderr 和可选的滚动文件 handler
def setup_logging(config:CopyClaudeConfig) -> None: # 函数用来设计日志的相关配置，以行为单位来保留上面提到的信息格式
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    # 从配置对象中读取 config.logging.level（如 "INFO"、"DEBUG"），通过 getattr 获取 logging 模块中对应的常量（如 logging.INFO）。
    fmt = _JSON_FMT if config.logging.format == "json" else _TEXT_FMT

    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")
    # 将格式字符串和日期格式（ISO 风格，如 2025-03-20T14:30:00）绑定到 Formatter 对象。
    root = logging.getLogger() # 根日志
    root.setLevel(level)
    root.handlers.clear()
    # root.handlers.clear()：清除已有的所有处理器（handlers）。这很重要，
    # 因为多次调用 setup_logging 时，如果不清理，会导致同一条日志被多个处理器重复输出（例如，代码在热重载或测试中多次初始化日志时）。

    stderr_handler = logging.StreamHandler(sys.stderr) # stderr 处理器（控制台输出）
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)
    # 创建一个输出到标准错误流（sys.stderr）的处理器。
    # 设置格式器并添加到 root logger。这样所有日志默认都会输出到终端。

    if config.logging.file:
        log_path = Path(config.logging.file).expanduser() # 日志路径设在C盘里
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
