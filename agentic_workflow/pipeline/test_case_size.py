"""
pipeline/test_case_size.py
==========================
How big a generated test case is (issue #3): Java source files and lines of
code under src/main/java (blank lines and comments not counted).
"""

import re
from dataclasses import dataclass
from pathlib import Path

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//.*")


@dataclass
class TestCaseSize:
    files: int
    lines: int

    def over(self, max_files: int, max_lines: int) -> bool:
        return self.files > max_files or self.lines > max_lines

    def as_dict(self) -> dict:
        return {"files": self.files, "lines": self.lines}

    def __str__(self) -> str:
        return f"{self.lines} lines of code in {self.files} Java files"


def code_lines(source: str) -> int:
    source = _BLOCK_COMMENT.sub("", source)
    return sum(1 for line in source.splitlines() if _LINE_COMMENT.sub("", line).strip())


def measure(test_case_dir: str | Path) -> TestCaseSize:
    src = Path(test_case_dir) / "src" / "main" / "java"
    files = sorted(src.rglob("*.java")) if src.exists() else []
    return TestCaseSize(len(files), sum(code_lines(f.read_text(errors="replace")) for f in files))
