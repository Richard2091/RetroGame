import subprocess
import sys
from pathlib import Path

mode = sys.argv[1] if len(sys.argv) > 1 else "all"
root = Path.cwd()
games_root = root / "games" / "fc"

paths = []
if mode == "non-rom":
    for p in games_root.rglob("*"):
        if p.is_file() and "roms" not in p.parts:
            paths.append(str(p))
elif mode == "rom-batch":
    # 批量参数：第三个参数为 start 索引（slug 排序后的段）
    start = int(sys.argv[2])
    size = int(sys.argv[3])
    slug_dirs = sorted([d for d in games_root.iterdir() if d.is_dir()])
    batch = slug_dirs[start:start + size]
    for d in batch:
        roms = d / "roms"
        if roms.is_dir():
            for f in sorted(roms.iterdir()):
                if f.is_file():
                    paths.append(str(f))
else:
    print("usage: git_stage.py non-rom | rom-batch <start> <size>", file=sys.stderr)
    sys.exit(2)

if not paths:
    print("no files staged")
    sys.exit(0)

data = "\0".join(paths).encode("utf-8") + b"\0"
proc = subprocess.run(
    ["git", "update-index", "--add", "-z", "--stdin"],
    input=data,
    cwd=root,
    capture_output=True,
)
if proc.returncode != 0:
    print(proc.stderr.decode("utf-8", "replace"), file=sys.stderr)
    sys.exit(proc.returncode)
print(f"staged {len(paths)} files")
