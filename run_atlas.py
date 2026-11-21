"""隔离模式入口：python3 -I run_atlas.py <atlas 子命令>。

数据目录 data/ 只含 JSON、不含 Python 模块；用 -I 运行可避免解释器从数据侧
加载被植入的模块。仓库路径在此显式登记，不依赖当前工作目录。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from atlas.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
