"""
Download the SA tools at the exact commits used in the experiments into tools/.

The commits are read from agentic_workflow/jcg/TC.Dockerfile (WALA_COMMIT_ID,
OPAL_COMMIT_ID, SOOT_COMMIT_ID), the image that builds the tools for the trace
check, so local checkouts and the experiments cannot drift apart. Each tool is
a shallow git checkout of that single commit, so `git -C tools/<tool> rev-parse
HEAD` shows the version.

Usage (from the repository root):
    python scripts/setup/download_tools.py [wala soot opal] [--dest tools]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "agentic_workflow" / "jcg" / "TC.Dockerfile"
URLS = {
    "wala": "https://github.com/wala/WALA.git",
    "opal": "https://github.com/stg-tud/opal.git",
    "soot": "https://github.com/soot-oss/soot.git",
}


def pinned_commits() -> dict[str, str]:
    text = DOCKERFILE.read_text()
    commits = {}
    for tool in URLS:
        m = re.search(rf'ARG {tool.upper()}_COMMIT_ID="([0-9a-f]{{40}})"', text)
        if not m:
            sys.exit(f"{tool.upper()}_COMMIT_ID not found in {DOCKERFILE}")
        commits[tool] = m.group(1)
    return commits


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def fetch(tool: str, commit: str, dest: Path) -> None:
    target = dest / tool
    if (target / ".git").exists():
        head = git("rev-parse", "HEAD", cwd=target)
        if head == commit:
            print(f"{tool}: already at {commit}")
            return
        sys.exit(f"{tool}: {target} is at {head}, expected {commit} — move it away and rerun")
    if target.exists() and any(target.iterdir()):
        sys.exit(f"{tool}: {target} exists and is not a git checkout — move it away and rerun")
    target.mkdir(parents=True, exist_ok=True)
    print(f"{tool}: fetching {commit} from {URLS[tool]} ...")
    git("init", "-q", cwd=target)
    git("remote", "add", "origin", URLS[tool], cwd=target)
    git("fetch", "-q", "--depth", "1", "origin", commit, cwd=target)
    git("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD", cwd=target)
    assert git("rev-parse", "HEAD", cwd=target) == commit
    print(f"{tool}: {target} at {commit}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tools", nargs="*", help=f"any of {', '.join(URLS)} (default: all)")
    ap.add_argument("--dest", type=Path, default=REPO / "tools")
    args = ap.parse_args()
    unknown = set(args.tools) - set(URLS)
    if unknown:
        ap.error(f"unknown tool(s): {', '.join(sorted(unknown))}; choose from {', '.join(URLS)}")
    commits = pinned_commits()
    for tool in args.tools or list(URLS):
        fetch(tool, commits[tool], args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
