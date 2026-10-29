"""Check local Tushare env without printing secrets."""

from __future__ import annotations

import sys
from pathlib import Path


OFFICIAL_POINTS = 2100
OFFICIAL_VALID_THROUGH = "2026-11-03"


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    values = _load_dotenv(root / ".env")
    broker = bool(values.get("SXSC_TUSHARE_TOKEN"))
    official = bool(values.get("TUSHARE_TOKEN"))
    try:
        import sxsc_tushare  # noqa: F401

        sdk = "installed"
    except ImportError:
        sdk = "missing"

    print(f"sxsc_tushare: {sdk}")
    print(f"SXSC_TUSHARE_TOKEN: {'set' if broker else 'missing'}")
    print(f"TUSHARE_TOKEN: {'set' if official else 'missing'}")
    print(f"official_points: {OFFICIAL_POINTS}")
    print(f"official_valid_through: {OFFICIAL_VALID_THROUGH}")
    print("mode: sdk" if sdk == "installed" else "mode: unavailable")
    if not broker:
        print("SXSC_TUSHARE_TOKEN is required before any broker pull.", file=sys.stderr)
        return 1
    if sdk != "installed":
        print("Install the repo with: pip install -e .", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
