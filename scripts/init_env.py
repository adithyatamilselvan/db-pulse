"""Generate a local .env with fresh secrets (never overwrites an existing file)."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".env"


def main() -> int:
    if TARGET.exists() and "--force" not in sys.argv:
        print(f"{TARGET} already exists; use --force to overwrite")
        return 1
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = {"SECRET_KEY": secrets.token_urlsafe(48), "ENCRYPTION_KEY": Fernet.generate_key().decode()}
    out = []
    for line in template.splitlines():
        key = line.split("=", 1)[0]
        out.append(f"{key}={values[key]}" if key in values else line)
    TARGET.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET} with newly generated SECRET_KEY and ENCRYPTION_KEY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
