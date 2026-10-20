"""发布前检查（CD 流水线用，也可以本地运行）：标签、代码里的版本号、CHANGELOG 三者一致，再取出这一版的更新记录。

    python scripts/release_check.py v0.21.0          # 正式版：三者必须一致
    python scripts/release_check.py v0.21.0-rc.1     # 预发布（试跑流水线用）：-rc.1 之前的部分和版本号一致
    python scripts/release_check.py                  # 不给标签：只检查版本号和 CHANGELOG（手动试跑）

通过时把这一版的更新记录写到 release_notes.md；在 GitHub Actions 里还会输出 version、tag、prerelease 给后面的步骤用。
不一致时打印原因、以非 0 退出，流水线就此停下，不会发布。
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "opc"


def code_version() -> str:
    text = (ROOT / PACKAGE / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        sys.exit(f"{PACKAGE}/__init__.py 里没有找到 __version__")
    return m.group(1)


def changelog_section(version: str) -> str:
    """CHANGELOG 里这一版的内容。最上面的一版必须就是它（发布的是最新一版）。"""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heads = list(re.finditer(r"^### v(\S+)", text, re.M))
    if not heads:
        sys.exit("CHANGELOG.md 里没有找到版本标题（### vX.Y.Z）")
    if heads[0].group(1) != version:
        sys.exit(f"CHANGELOG.md 最上面一版是 v{heads[0].group(1)}，代码里的版本是 {version}：先写这一版的更新记录")
    end = heads[1].start() if len(heads) > 1 else len(text)
    return text[heads[0].start():end].strip()


def main() -> None:
    tag = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    version = code_version()
    prerelease = False
    if tag:
        m = re.fullmatch(r"v(\d+\.\d+\.\d+)(-[0-9A-Za-z.]+)?", tag)
        if not m:
            sys.exit(f"标签 {tag} 的格式不对：要写成 v1.2.3，试跑用 v1.2.3-rc.1")
        if m.group(1) != version:
            sys.exit(f"标签是 {tag}，代码里的版本是 {version}：改好版本号再打标签")
        prerelease = bool(m.group(2))
    notes = changelog_section(version)
    (ROOT / "release_notes.md").write_text(notes + "\n", encoding="utf-8")
    print(f"检查通过：版本 {version}" + (f"，标签 {tag}" if tag else "，没有标签（只检查）")
          + ("，预发布" if prerelease else ""))
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"version={version}\ntag={tag}\nprerelease={'true' if prerelease else 'false'}\n")


if __name__ == "__main__":
    main()
