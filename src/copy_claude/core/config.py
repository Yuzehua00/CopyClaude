from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from dotenv import load_dotenv


'--------------------------------------------------默认设置--------------------------------------------------------'
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7437
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_LOG_FILE = "~/.copyclaude/logs/core.log"
_DEFAULT_LOG_FORMAT = "text"
_DEFAULT_CONFIG_PATH = "~/.copyclaude/config.toml"
_DEFAULT_MAX_STEPS = 20
_DEFAULT_MODEL = "deepseek-v4-flash"
_DEFAULT_TRACE_FILE = "~/.copyclaude/traces/daemon.jsonl"
@dataclass
class LoggingConfig: # 日志配置
    level: str = _DEFAULT_LOG_LEVEL # 日志输出级别（DEBUG / INFO / WARNING / ERROR），控制日志详细程度。
    file: str = _DEFAULT_LOG_FILE # 日志文件保存路径（~ 会展开为用户目录）
    format: str = _DEFAULT_LOG_FORMAT  # "text" | "json" text用于人类读取，json用于机器识别
@dataclass
class AgentConfig:
    # 代理（Agent）在一次任务中最多执行的推理步数。每一步可能包含一次 LLM 调用+工具执行，达到上限后任务自动停止，防止无限循环。
    max_steps: int = _DEFAULT_MAX_STEPS
@dataclass
class LlmConfig: # 大语言模型设置，涉及模型名称，多模型路由策略。
    default_model: str = _DEFAULT_MODEL
    router: str = "static"  # "static" | "rule_based" (S4) | "cost_budget" (S6)
    # 路由就是决定“把当前这个请求交给哪个模型来处理”。
    # static 固定使用默认模型；rule_based 按规则切换（如根据问题类型选模型）；cost_budget 根据预算动态选择。
@dataclass
class TraceConfig:
    enabled: bool = True
    file: str = _DEFAULT_TRACE_FILE # 追踪文件路径（JSONL 格式，每行一个事件）。
    include_llm_payload: bool = True  # false 时 LLM 记录只保留摘要

@dataclass
class PermissionConfig: # Agent执行危险行为时要交给用户审批，超时代表不通过
    timeout_s: float = 60.0  # 审批超时秒数；0 表示不超时

@dataclass
class CompactionConfig: # 上下文压缩配置，
    auto_threshold: float = 0.0    # context_pct 触发自动压缩的阈值（0 表示禁用，推荐用手动 /compact）
    tool_result_limit: int = 8_000  # tool_result 截断触发字符数
    tool_result_keep: int = 4_000   # 截断后保留的前缀字符数

@dataclass
class McpServerConfig: # MCP（Model Context Protocol）允许代理连接外部工具服务器（如文件系统、数据库、API 网关）。
    # API 网关是位于客户端和后端服务之间的“统一门卫”，负责所有请求的接入与转发。‌ 它作为中间层组件，隐藏了后端复杂架构，让外部调用更简单安全 。
    name: str
    transport: str = "stdio"       # "stdio" | "tcp"
    # transport：通信方式。
    # stdio：通过子进程标准输入输出通信（适合本地可执行文件）。
    # tcp：通过 TCP socket 连接远程服务器
    command: str = ""              # stdio 专用：可执行文件路径
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    host: str = "localhost"        # tcp 专用
    port: int = 3000               # tcp 专用

@dataclass
class McpConfig: # MCPServerConfig列表
    servers: list[McpServerConfig] = field(default_factory=list)
@dataclass
class CopyClaudeConfig: # 总体config，涉及所有相关配置
    host: str = _DEFAULT_HOST # 主机
    port: int = _DEFAULT_PORT # 端口号
    logging: LoggingConfig = field(default_factory=LoggingConfig) # 日志记录设置，涉及记录级别，存储路径和格式
    agent: AgentConfig = field(default_factory=AgentConfig) # 代理（Agent）在一次任务中最多执行的推理步数。
    llm: LlmConfig = field(default_factory=LlmConfig) # 大语言模型设置，涉及模型名称，多模型路由策略。
    trace: TraceConfig = field(default_factory=TraceConfig)
    permission: PermissionConfig = field(default_factory=PermissionConfig) # Agent执行危险行为时要交给用户审批，超时代表不通过
    compaction: CompactionConfig = field(default_factory=CompactionConfig) # 上下文压缩配置，
    mcp: McpConfig = field(default_factory=McpConfig)# MCP（Model Context Protocol）允许代理连接外部工具服务器（如文件系统、数据库、API 网关）。

def get_config()->CopyClaudeConfig:
    # 按优先级从低到高依次加载配置，后加载的覆盖先加载的，最终返回一个完整的 copyConfig 对象。
    config = CopyClaudeConfig() # 优先级最低，初始化赋值。

    # .env 必须在读取 COPYCLAUDE_CONFIG 之前加载，以便 .env 中的 COPYCLAUDE_CONFIG 能影响 TOML 路径

    # 使用 __file__ 构建绝对路径，避免 CWD 不同导致 .env 找不到
    dotenv_path = Path(__file__).resolve().parents[3] / ".env"
    load_success = load_dotenv(dotenv_path=dotenv_path, verbose=True, override=False) # 将.env的键=值对加载进环境变量中，可以直接用os.environ.get(键)得到值

    explicit = os.environ.get("COPYCLAUDE_CONFIG")
    if explicit: # 如果env中设置了COPYCLAUDE_CONFIG，就将config_paths设置为env里有的
        config_paths = [Path(explicit).expanduser()]
    else: # 否则config_paths提供默认路径以及项目文件自带的相对路径。
        config_paths = [
            Path(_DEFAULT_CONFIG_PATH).expanduser(),
            Path(".copyclaude/config.toml"),
        ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, "rb") as f:
                    data = tomllib.load(f) # 加载涉及设置的toml文件。
                    # tomllib.load(f) 会读取一个 TOML 格式的文件对象 f，并将其解析成一个 Python 字典（dict）。
            except tomllib.TOMLDecodeError as e:
                raise SystemExit(f"Config parse error ({config_path}): {e}") from e
            _apply_toml(config, data)

    _apply_env(config)
    return config

def _apply_toml(config: CopyClaudeConfig, data: dict[str, Any]) -> None: # data来源：config.toml
    unknown = set(data.keys()) - {"core", "logging", "agent", "llm", "trace", "permission", "compaction", "mcp"}
    if unknown:
        raise SystemExit(f"Unknown top-level config keys: {', '.join(sorted(unknown))}")
    # data内部数据格式均符合上面定义的数据类
    if "core" in data: # data中的core记录host和port的信息，做了很多的报错机制检查host是str,port是int
        core = data["core"]
        if not isinstance(core, dict): # isinstance() 是 Python 内置函数，用来检查一个对象是不是某个类型或其子类的实例‌，返回 True 或 False。‌
            raise SystemExit("Config error: [core] must be a table")
        unknown_core: set[str] = set(core.keys()) - {"host", "port"}
        if unknown_core:
            raise SystemExit(f"Unknown [core] keys: {', '.join(sorted(unknown_core))}")
        if "host" in core:
            val = core["host"]
            if not isinstance(val, str):
                raise SystemExit("Config error: core.host must be a string")
            config.host = val # 提取的host存入config.host
        if "port" in core:
            val = core["port"]
            if not isinstance(val, int):
                raise SystemExit("Config error: core.port must be an integer")
            config.port = val # 提取的port存入config.port

    if "logging" in data: # data[logging]涉及日志配置
        log = data["logging"]
        if not isinstance(log, dict):
            raise SystemExit("Config error: [logging] must be a table")
        unknown_log: set[str] = set(log.keys()) - {"level", "file", "format"}
        if unknown_log:
            raise SystemExit(f"Unknown [logging] keys: {', '.join(sorted(unknown_log))}")
        for key in ("level", "file", "format"):
            if key in log:
                val = log[key]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: logging.{key} must be a string")
                setattr(config.logging, key, val) # setattr() 是 Python 内置函数，用于通过字符串动态设置对象的属性值‌，
                # 语法为setattr(object, name, value)，无需返回值，直接修改对象状态。‌

    if "llm" in data:
        llm = data["llm"]
        if not isinstance(llm, dict):
            raise SystemExit("Config error: [llm] must be a table")
        unknown_llm: set[str] = set(llm.keys()) - {"default_model", "router"}
        if unknown_llm:
            raise SystemExit(f"Unknown [llm] keys: {', '.join(sorted(unknown_llm))}")
        if "default_model" in llm:
            val = llm["default_model"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.default_model must be a string")
            config.llm.default_model = val
        if "router" in llm:
            val = llm["router"]
            if not isinstance(val, str):
                raise SystemExit("Config error: llm.router must be a string")
            config.llm.router = val

    if "trace" in data:
        trace = data["trace"]
        if not isinstance(trace, dict):
            raise SystemExit("Config error: [trace] must be a table")
        unknown_trace: set[str] = set(trace.keys()) - {"enabled", "file", "include_llm_payload"}
        if unknown_trace:
            raise SystemExit(f"Unknown [trace] keys: {', '.join(sorted(unknown_trace))}")
        if "enabled" in trace:
            val = trace["enabled"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: trace.enabled must be a boolean")
            config.trace.enabled = val
        if "file" in trace:
            val = trace["file"]
            if not isinstance(val, str):
                raise SystemExit("Config error: trace.file must be a string")
            config.trace.file = val
        if "include_llm_payload" in trace:
            val = trace["include_llm_payload"]
            if not isinstance(val, bool):
                raise SystemExit("Config error: trace.include_llm_payload must be a boolean")
            config.trace.include_llm_payload = val

    if "permission" in data:
        perm = data["permission"]
        if not isinstance(perm, dict):
            raise SystemExit("Config error: [permission] must be a table")
        unknown_perm: set[str] = set(perm.keys()) - {"timeout_s"}
        if unknown_perm:
            raise SystemExit(f"Unknown [permission] keys: {', '.join(sorted(unknown_perm))}")
        if "timeout_s" in perm:
            val = perm["timeout_s"]
            if not isinstance(val, (int, float)) or val < 0:
                raise SystemExit("Config error: permission.timeout_s must be a non-negative number")
            config.permission.timeout_s = float(val)

    if "compaction" in data:
        comp = data["compaction"]
        if not isinstance(comp, dict):
            raise SystemExit("Config error: [compaction] must be a table")
        unknown_comp: set[str] = set(comp.keys()) - {"auto_threshold", "tool_result_limit", "tool_result_keep"}
        if unknown_comp:
            raise SystemExit(f"Unknown [compaction] keys: {', '.join(sorted(unknown_comp))}")
        if "auto_threshold" in comp:
            val = comp["auto_threshold"]
            if not isinstance(val, (int, float)) or not (0.0 <= val <= 1.0):
                raise SystemExit("Config error: compaction.auto_threshold must be between 0 and 1")
            config.compaction.auto_threshold = float(val)
        if "tool_result_limit" in comp:
            val = comp["tool_result_limit"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: compaction.tool_result_limit must be a positive integer")
            config.compaction.tool_result_limit = val
        if "tool_result_keep" in comp:
            val = comp["tool_result_keep"]
            if not isinstance(val, int) or val <= 0:
                raise SystemExit("Config error: compaction.tool_result_keep must be a positive integer")
            config.compaction.tool_result_keep = val

    if "mcp" in data:
        mcp = data["mcp"]
        if not isinstance(mcp, dict):
            raise SystemExit("Config error: [mcp] must be a table")
        unknown_mcp: set[str] = set(mcp.keys()) - {"servers"}
        if unknown_mcp:
            raise SystemExit(f"Unknown [mcp] keys: {', '.join(sorted(unknown_mcp))}")
        servers_raw = mcp.get("servers", [])
        if not isinstance(servers_raw, list):
            raise SystemExit("Config error: mcp.servers must be an array of tables")
        for i, srv in enumerate(servers_raw): # 遍历mcp列表的mcp工具
            if not isinstance(srv, dict):
                raise SystemExit(f"Config error: mcp.servers[{i}] must be a table")
            name = srv.get("name")
            if not isinstance(name, str) or not name:
                raise SystemExit(f"Config error: mcp.servers[{i}].name must be a non-empty string")
            transport = srv.get("transport", "stdio")
            if transport not in ("stdio", "tcp"):
                raise SystemExit(f"Config error: mcp.servers[{i}].transport must be 'stdio' or 'tcp'")
            s = McpServerConfig(name=name, transport=transport)
            if "command" in srv:
                val = srv["command"]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: mcp.servers[{i}].command must be a string")
                s.command = val
            if "args" in srv:
                val = srv["args"]
                if not isinstance(val, list):
                    raise SystemExit(f"Config error: mcp.servers[{i}].args must be an array")
                s.args = [str(a) for a in val]
            if "env" in srv:
                val = srv["env"]
                if not isinstance(val, dict):
                    raise SystemExit(f"Config error: mcp.servers[{i}].env must be a table")
                s.env = {str(k): str(v) for k, v in val.items()}
            if "host" in srv:
                val = srv["host"]
                if not isinstance(val, str):
                    raise SystemExit(f"Config error: mcp.servers[{i}].host must be a string")
                s.host = val
            if "port" in srv:
                val = srv["port"]
                if not isinstance(val, int):
                    raise SystemExit(f"Config error: mcp.servers[{i}].port must be an integer")
                s.port = val
            config.mcp.servers.append(s)
def _apply_env(config: CopyClaudeConfig)->None:
# 用 COPYCLAUDE_* 环境变量覆盖 config 中对应字段（若变量已设置）,.env的内容已经通过load_dotenv函数全放进环境变量里了
    host = os.environ.get("COPYCLAUDE_HOST")
    if host is not None:
        config.host = host

    port_str = os.environ.get("COPYCLAUDE_PORT")
    if port_str is not None:
        try:
            config.port = int(port_str)
        except ValueError:
            raise SystemExit(f"Config error: COPYCLAUDE_PORT must be an integer, got: {port_str!r}")

    log_level = os.environ.get("COPYCLAUDE_LOG_LEVEL")
    if log_level is not None:
        config.logging.level = log_level

    log_file = os.environ.get("COPYCLAUDE_LOG_FILE")
    if log_file is not None:
        config.logging.file = log_file

    log_format = os.environ.get("COPYCLAUDE_LOG_FORMAT")
    if log_format is not None:
        config.logging.format = log_format

    max_steps_str = os.environ.get("COPYCLAUDE_MAX_STEPS")
    if max_steps_str is not None:
        try:
            val = int(max_steps_str)
            if val <= 0:
                raise SystemExit(
                    "Config error: COPYCLAUDE_MAX_STEPS must be a positive integer,"
                    f" got: {max_steps_str!r}"
                )
            config.agent.max_steps = val
        except ValueError:
            raise SystemExit(
                f"Config error: COPYCLAUDE_MAX_STEPS must be an integer, got: {max_steps_str!r}"
            )

    default_model = os.environ.get("COPYCLAUDE_LLM_DEFAULT_MODEL")
    if default_model is not None:
        config.llm.default_model = default_model

    trace_enabled = os.environ.get("COPYCLAUDE_TRACE_ENABLED")
    if trace_enabled is not None:
        config.trace.enabled = trace_enabled.lower() not in ("0", "false", "no")

    trace_file = os.environ.get("COPYCLAUDE_TRACE_FILE")
    if trace_file is not None:
        config.trace.file = trace_file

    trace_payload = os.environ.get("COPYCLAUDE_TRACE_INCLUDE_LLM_PAYLOAD")
    if trace_payload is not None:
        config.trace.include_llm_payload = trace_payload.lower() not in ("0", "false", "no")

    perm_timeout = os.environ.get("COPYCLAUDE_PERMISSION_TIMEOUT_S")
    if perm_timeout is not None:
        try:
            perm_timeout_val = float(perm_timeout)
            if perm_timeout_val < 0:
                raise SystemExit(
                    f"Config error: COPYCLAUDE_PERMISSION_TIMEOUT_S must be >= 0, got: {perm_timeout!r}"
                )
            config.permission.timeout_s = perm_timeout_val
        except ValueError:
            raise SystemExit(
                f"Config error: COPYCLAUDE_PERMISSION_TIMEOUT_S must be a number, got: {perm_timeout!r}"
            )

    compact_threshold = os.environ.get("COPYCLAUDE_COMPACT_THRESHOLD")
    if compact_threshold is not None:
        try:
            compact_threshold_val = float(compact_threshold)
            if not (0.0 <= compact_threshold_val <= 1.0):
                raise SystemExit(
                    f"Config error: COPYCLAUDE_COMPACT_THRESHOLD must be between 0 and 1, got: {compact_threshold!r}"
                )
            config.compaction.auto_threshold = compact_threshold_val
        except ValueError:
            raise SystemExit(
                f"Config error: COPYCLAUDE_COMPACT_THRESHOLD must be a number, got: {compact_threshold!r}"
            )

    compact_tool_limit = os.environ.get("COPYCLAUDE_COMPACT_TOOL_LIMIT")
    if compact_tool_limit is not None:
        try:
            compact_tool_limit_val = int(compact_tool_limit)
            if compact_tool_limit_val <= 0:
                raise SystemExit(
                    f"Config error: COPYCLAUDE_COMPACT_TOOL_LIMIT must be a positive integer, got: {compact_tool_limit!r}"
                )
            config.compaction.tool_result_limit = compact_tool_limit_val
        except ValueError:
            raise SystemExit(
                f"Config error: COPYCLAUDE_COMPACT_TOOL_LIMIT must be an integer, got: {compact_tool_limit!r}"
            )

    compact_tool_keep = os.environ.get("COPYCLAUDE_COMPACT_TOOL_KEEP")
    if compact_tool_keep is not None:
        try:
            compact_tool_keep_val = int(compact_tool_keep)
            if compact_tool_keep_val <= 0:
                raise SystemExit(
                    f"Config error: COPYCLAUDE_COMPACT_TOOL_KEEP must be a positive integer, got: {compact_tool_keep!r}"
                )
            config.compaction.tool_result_keep = compact_tool_keep_val
        except ValueError:
            raise SystemExit(
                f"Config error: COPYCLAUDE_COMPACT_TOOL_KEEP must be an integer, got: {compact_tool_keep!r}"
            )
