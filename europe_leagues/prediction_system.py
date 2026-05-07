#!/usr/bin/env python3
"""模块说明：提供兼容命令入口，内部将所有 CLI 调用转发到 app/cli.py。

兼容入口，内部路由已迁移到 app/cli.py。"""

from app.cli import build_parser, main

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    main()
