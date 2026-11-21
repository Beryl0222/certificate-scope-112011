"""支持 ``python3 -I -m sampling`` 直接运行（隔离模式下显式加入仓库根目录）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sampling.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
