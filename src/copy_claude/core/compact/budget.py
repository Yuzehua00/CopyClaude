# 为了避免工具结果消耗过多的token，导致上下文暴涨填满窗口，在这里将内存中的messages中的工具结果截断。但不破坏保存在日志的工具结果。

TOOL_RESULT_LIMIT = 8_000
TOOL_RESULT_KEEP = 4_000
def truncate_tool_results(messages, limit=TOOL_RESULT_LIMIT, keep=TOOL_RESULT_KEEP):
    result = []
    for msg in messages: # S6遍历所有信息，工具结果只会在role:user里并且type是tool_result。
        if msg.get("role") != "user":
            result.append(msg)
            continue
        content = msg.get("content") # S6content不是列表就是一句话。那说明是用户打的字。因此不用处理
        if not isinstance(content, list):
            result.append(msg)
            continue
        new_blocks = []
        for block in content:
            if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                text = block["content"]
                if len(text) > limit:
                    omitted = len(text) - keep
                    block = dict(block)
                    block["content"] = (
                        text[:keep]
                        + f"\n[... {omitted} chars omitted. Full output in run events.]"
                    )
            new_blocks.append(block)
        result.append({**msg, "content": new_blocks})
    return result