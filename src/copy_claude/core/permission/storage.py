from __future__ import annotations
from pathlib import Path
import json

_DEFAULT_POLICY_PATH = Path("~/.copyclaude/policy.toml")


def load_policy_file(path: Path | None = None) -> dict[str, str]:
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    if not p.exists():
        return {}
    result: dict[str, str] = {}
    in_always = False
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[always]":
            in_always = True
            continue
        if stripped.startswith("["):
            in_always = False
            continue
        if in_always and "=" in stripped and not stripped.startswith("#"):  # 进入in_always，同时有=，又不是注释。
            k, _, v = stripped.partition("=")
            k = k.strip()
            v = v.strip().strip('"')
            if v in ("allow", "deny"): # 检查是否满足v在allow和deny的条件。
                result[k] = v
    return result


# 将 {tool_name: "allow"/"deny"} 写入 policy.toml，覆盖 [always] 节
def save_policy_file(always: dict[str, str], path: Path | None = None) -> None:  # policy文件格式是[always]下面按=左右为键值对
    p = (path or _DEFAULT_POLICY_PATH).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ~/.kama/policy.toml",
        "# 由 kama-core 自动管理，手动编辑生效但格式须正确",
        "",
        "[always]",
    ]
    for tool_name, decision in sorted(always.items()):
        lines.append(f"{tool_name}={decision}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
