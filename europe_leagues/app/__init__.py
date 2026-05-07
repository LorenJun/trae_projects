"""模块说明：初始化 app 包并暴露命令行实现相关入口。"""

from .cli import build_parser, main

__all__ = ["build_parser", "main"]
