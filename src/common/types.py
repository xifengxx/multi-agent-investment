"""项目公共类型定义。"""

from __future__ import annotations

from typing import Literal

RunMode = Literal["daily", "weekly"]
AppEnv = Literal["dev", "test", "prod"]
