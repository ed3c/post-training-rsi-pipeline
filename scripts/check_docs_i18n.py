#!/usr/bin/env python3
"""Validate maintained English / Traditional Chinese Markdown pairs."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "i18n-manifest.json"
META_RE = re.compile(
    r"<!--\s*i18n-key:\s*(?P<key>[A-Z0-9_]+);\s*locale:\s*(?P<locale>en|zh-TW);"
)


def fail(message: str) -> None:
    print(f"docs-i18n: {message}", file=sys.stderr)


def main() -> int:
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {MANIFEST.relative_to(ROOT)}: {exc}")
        return 1

    errors: list[str] = []
    seen_paths: set[str] = set()
    seen_keys: set[str] = set()

    for item in payload.get("pairs", []):
        key = item.get("key")
        english = item.get("english")
        zh_tw = item.get("zh-TW")
        if not all(isinstance(value, str) and value for value in (key, english, zh_tw)):
            errors.append(f"invalid pair entry: {item!r}")
            continue
        if key in seen_keys:
            errors.append(f"duplicate i18n key: {key}")
        seen_keys.add(key)

        for path_text, locale, peer_text in (
            (english, "en", zh_tw),
            (zh_tw, "zh-TW", english),
        ):
            if path_text in seen_paths:
                errors.append(f"document appears in multiple pairs: {path_text}")
            seen_paths.add(path_text)
            path = ROOT / path_text
            if not path.is_file():
                errors.append(f"missing {locale} document: {path_text}")
                continue

            text = path.read_text(encoding="utf-8")
            first_lines = "\n".join(text.splitlines()[:20])
            meta = META_RE.search(first_lines)
            if not meta:
                errors.append(f"missing i18n metadata in first 20 lines: {path_text}")
            elif meta.group("key") != key or meta.group("locale") != locale:
                errors.append(
                    f"metadata mismatch in {path_text}: "
                    f"expected key={key!r}, locale={locale!r}"
                )

            peer_name = Path(peer_text).name
            if peer_name not in first_lines:
                errors.append(
                    f"missing reciprocal language link to {peer_name!r}: {path_text}"
                )

    for path_text in payload.get("bilingual_inline", []):
        path = ROOT / path_text
        if not path.is_file():
            errors.append(f"missing bilingual-inline document: {path_text}")

    if errors:
        for error in errors:
            fail(error)
        return 1

    print(
        f"docs-i18n: validated {len(payload.get('pairs', []))} pairs "
        f"and {len(payload.get('bilingual_inline', []))} bilingual-inline files"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
