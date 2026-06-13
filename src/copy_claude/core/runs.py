# 此函数库用于文件运行所需数据生
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
RUNS_DIR = Path("runs")
def new_run_id():
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{ts}-{suffix}"