from __future__ import annotations
import argparse
import logging
import logging.handlers
import os
from pathlib import Path
# tui和cli结构类似都需要解析指令。
from copy_claude.core.config import get_config
from copy_claude.tui.app import CopyClaudeTuiApp
_DEFAULT_TUI_LOG = "~/.copyclaude/logs/tui.log"
def _setup_logging(level:str) -> None:
    # 获取日志存放路径
    log_path = Path(os.environ.get('COPYCLAUDE_LOG_FILE', _DEFAULT_TUI_LOG)).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True) # 保证有文件夹可以存储。

    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=5*1024*1024, backupCount=3,encoding="utf-8")
    # 是利用 Python 内置的 RotatingFileHandler 来创建一个能自动按大小“分卷”的日志管理器。
    # 它的核心功能是：当天志文件（app.log）快写到 5 MB 时，会自动把它“封存”成历史文件（app.log.1），并创建一个新的 app.log 继续写，
    # 同时只保留最近 3 个历史文件-22。这是一种基础但有效的手段，用来防止单个日志文件无限制地膨胀，耗尽磁盘空间--19。
    handler.setFormatter(
        logging.Formatter(
            'level=%(levelname)s ts=%(asctime)s source=%(name)s msg="%(message)s"',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    # handler.setFormatter(...) 的作用是为日志处理器（Handler）绑定一个日志格式器（Formatter）
    # ，从而定义每一条日志消息最终以什么样的字符串格式被写入到文件或输出到控制台。
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

def main():
    config = get_config()
    parser = argparse.ArgumentParser(prog='copyclaude-tui',description="本地Agent的tui")
    parser.add_argument("--replay",
                        metavar="RUN_ID",
                        help="从过去连接过的运行结果回放事件",) # 要回放必须已知过去的run_id
    args = parser.parse_args()
    _setup_logging(config.logging.level)
    app = CopyClaudeTuiApp(config.host, config.port, replay_run_id=args.replay)
    app.run() # 这是父类的函数。

if __name__ == "__main__":
    main()