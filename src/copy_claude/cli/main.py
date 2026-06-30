import argparse
import sys

from copy_claude.cli.commands.version import cmd_version
from copy_claude.cli.commands.ping import cmd_ping
from copy_claude.cli.commands.run import cmd_run
from copy_claude.cli.commands.trace import cmd_trace
from copy_claude.cli.commands.chat import cmd_chat
from copy_claude.core.config import get_config
from copy_claude.core.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="copyclaude",description="自制Claude的CLI，解析用户的指令")
    # 表现在命令行中就是输入copyclaude+命令字段之后可以在args里访问字段。

    parser.add_argument("--version", action="store_true",help="打印version并退出")
    # add_argument可以创建args.version，并根据action = "store_true"将这一变量设为true。

    subparsers = parser.add_subparsers(dest="command")
    # 子命令容器，用args.command可以访问命令行的输入。如copyclaude run,就可以用args.command=run

    subparsers.add_parser("ping", help="显示与Core的连接Ping值")
    # 子命令与子命令容器的关系，子命令容器是左值（变量），子命令是右值（具体的字符串），copyclaude ping->args.command = ping
    # 根据args.command = 什么分别执行不同的函数。

    run_parser = subparsers.add_parser("run",help="运行llm规划并解决用户问题")
    run_parser.add_argument("--goal",required=True,help="用户提出的需求")


    # trace部分的命令
    trace_parser = subparsers.add_parser("trace",help="追踪内部信息流")
    trace_parser.add_argument("run_id", nargs="?", default=None, help="通过run_id过滤信息流记录")
    trace_parser.add_argument("--layer",choices=["ipc","event","llm"],help="根据层级类型追踪信息")
    trace_parser.add_argument("--direction",
                              choices=["CLIENT->CORE", "CORE->CLIENT","CORE","CORE->LLM","LLM->CORE"],
                              help="根据信息流向追踪信息")
    trace_parser.add_argument("--raw", action="store_true", help="输出行NDJSON")
    trace_parser.add_argument("--follow", "-f", action="store_true", help="Follow new records")
    args = parser.parse_args() # args读取用户在命令行的设置

    if args.version: # 负责执行用户命令copyclaude --version，返回版本号
        cmd_version()
        return
    config = get_config()
    setup_logging(config)

    if args.command == "ping":
        # 处理命令为ping的函数。
        cmd_ping(config)
    elif args.command == "run":
        cmd_run(args.goal,config) # 调起AgentLoop，终端输出显示，
    elif args.command == "chat":
        cmd_chat(config)
    elif args.command == "trace": # 这个命令是一个查询已trace文件的命令。并不涉及控制后端。
        cmd_trace(
            args.run_id,
            config,
            layer=args.layer,
            direction=args.direction,
            raw=args.raw,
            follow=args.follow
        )
    else:
        parser.print_help()
        sys.exit(1)
