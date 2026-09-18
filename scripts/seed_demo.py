from __future__ import annotations

from scripts.demo_common import safe_print, write_seed_pdfs


def main() -> int:
    target = write_seed_pdfs()
    safe_print(f"seed_demo_ready path={target}")
    safe_print("seed_files=success.pdf,low_confidence.pdf,provider_failure.pdf,invalid_total.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
