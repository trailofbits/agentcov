# /// script
# requires-python = ">=3.11"
# ///
"""Build dist/agentcov.pyz from the wheel that `uv build` left in dist/.

agentcov has no runtime dependencies, so the wheel's package contents plus a
top-level __main__.py make a complete zipapp that runs on any Python 3.11+.
"""

from __future__ import annotations

import shutil
import sys
import zipapp
import zipfile
from pathlib import Path

DIST = Path("dist")
STAGING = Path("build") / "zipapp-staging"
MAIN_PY = "from agentcov.cli import main\n\nraise SystemExit(main())\n"


def build() -> Path:
    wheels = sorted(DIST.glob("agentcov-*-py3-none-any.whl"))
    if not wheels:
        sys.exit("no agentcov wheel in dist/; run `uv build` first")
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)
    # The .dist-info metadata rides along so importlib.metadata keeps working
    # inside the zipapp.
    with zipfile.ZipFile(wheels[-1]) as wheel:
        wheel.extractall(STAGING)
    (STAGING / "__main__.py").write_text(MAIN_PY, encoding="utf-8")
    target = DIST / "agentcov.pyz"
    zipapp.create_archive(
        STAGING,
        target,
        interpreter="/usr/bin/env python3",
        compressed=True,
    )
    return target


if __name__ == "__main__":
    print(build())
