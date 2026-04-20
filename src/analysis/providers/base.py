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

    @property
    def supports_file_input(self) -> bool:
        """声明 provider 是否支持附件输入。"""
        return False

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """执行“prompt + 文件附件”调用，默认不支持并抛出明确异常。"""
        raise NotImplementedError("file_input_not_supported")
