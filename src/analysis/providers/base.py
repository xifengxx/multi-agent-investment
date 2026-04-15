"""LLM Provider 抽象接口定义。

为个人项目保持简单：Provider 只负责“给定 prompt -> 返回 raw_text”。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """返回 provider 名称（用于落库与追踪）。"""

    @abstractmethod
    def invoke(self, prompt: str) -> str:
        """调用 LLM 并返回原始文本输出。"""

