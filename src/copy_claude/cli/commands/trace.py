from __future__ import annotations

from copy_claude.core.config import CopyClaudeConfig
from copy_claude.core.trace.record import TraceRecord
from pathlib import Path
import sys
import json
import time

_COLORS = {
    "CLIENT→CORE": "\033[36m",
    "CORE→CLIENT": "\033[33m",
    "CORE": "\033[32m",
    "CORE→LLM": "\033[35m",
    "LLM→CORE": "\033[34m",
}
_RESET = "\033[0m"
_BOLD = "\033[1m"


def cmd_trace(run_id: str,
              config: CopyClaudeConfig,
              *,
              layer: str | None = None,
              direction: str | None = None,
              raw: bool = False,
              follow: bool = True) -> None:
    trace_path = Path(config.trace.file).expanduser()
    if not trace_path.exists():
        print(f"追踪文件未找到: {trace_path}", file=sys.stderr)
        sys.exit(1)

    with open(trace_path) as f:
        for line in f:
            _process_line(
                line.strip(),
                layer=layer,
                direction=direction,
                raw=raw,
                run_id=run_id
            )
    if follow:  # （实时跟踪文件新增内容）
        with open(trace_path) as f:
            f.seek(0, 2)  # 将文件指针定位到文件末尾
            while True:  #进入一个无限循环，反复尝试读取新的一行。
                line = f.readline()
                if line:  #如果有新行，立即处理（通过 _process_line）。
                    _process_line(
                        line.strip(),
                        run_id=run_id,
                        layer=layer,
                        direction=direction,
                        raw=raw,
                    )
                else:  #如果没有新行，短暂休眠（time.sleep(0.05)）以避免疯狂轮询浪费 CPU，然后继续循环。
                    time.sleep(0.05)


def _process_line(line: str,  # 处理单行的程序
                  *,
                  run_id: str | None = None,
                  layer: str | None = None,
                  direction: str | None = None,
                  raw: bool = False) -> None:
    if not line:
        return
    try:
        record = TraceRecord.model_validate(json.loads(line))
    except Exception:
        return

    if run_id is not None and record.run_id != run_id:
        return
    if layer is not None and record.layer != layer:
        return
    if direction is not None and record.direction != direction:
        return
    if raw:
        print(line)
    else:
        _print_record(record)


def _print_record(record: TraceRecord) -> None:  # 打印彩色的单行
    color = _COLORS.get(record.direction, "")
    ts = record.ts[11:23] if len(record.ts) >= 23 else record.ts

    direction_str = f"{color}{_BOLD}{record.direction:<14}{_RESET}"
    kind_str = f"{record.kind:<13}"

    parts: list[str] = []
    if record.run_id:
        parts.append(f"run={record.run_id[:8]}")
    if record.step is not None:
        parts.append(f"step={record.step}")
    parts.append(_summarize(record))

    print(f"{ts}  {direction_str}  {kind_str}  {'  '.join(parts)}")


def _summarize(record: TraceRecord) -> str:  # 压缩记录
    data = record.data
    kind = record.kind

    if kind == "command":
        params = data.get("params", {})
        goal = str(params.get("goal", ""))
        suffix = f'  goal="{goal[:50]}"' if goal else ""
        return f"method={data.get('method')}{suffix}"

    if kind == "response":
        result = data.get("result", {})
        if isinstance(result, dict) and "run_id" in result:
            return f"run_id={result['run_id'][:8]}"
        return str(result)[:60]

    if kind == "error":
        err = data.get("error", {})
        return f"code={err.get('code')}  {err.get('message', '')}"

    if kind == "push":
        return f"event={data.get('event_type')}  sub={data.get('sub_id')}"

    if kind == "event":
        return f"type={data.get('type')}"

    if kind == "api_call":
        msgs = data.get("messages")
        count = len(msgs) if isinstance(msgs, list) else data.get("message_count", "?")
        tools = data.get("tool_schemas")
        tc = len(tools) if isinstance(tools, list) else data.get("tool_count", "?")
        return f"msgs={count}  tools={tc}"

    if kind == "api_response":
        usage = data.get("usage", {})
        return (
            f"stop={data.get('stop_reason')}  "
            f"latency={data.get('latency_ms')}ms  "
            f"out_tokens={usage.get('output_tokens', '?')}"
        )

    return str(data)[:60]
