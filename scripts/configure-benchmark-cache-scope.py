#!/usr/bin/env python3
"""Replace checked-in cache placeholders with one benchmark cohort's tags."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
PLACEHOLDERS = {
    "immich-base-images-local": "docker_tag",
    "immich-base-images-ccache-local": "ccache_tag",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--docker-tag", required=True)
    parser.add_argument("--ccache-tag", required=True)
    args = parser.parse_args()

    replacements = {
        placeholder: getattr(args, argument)
        for placeholder, argument in PLACEHOLDERS.items()
    }
    for tag in replacements.values():
        if not TAG_PATTERN.fullmatch(tag):
            raise SystemExit(f"Unsafe cache tag: {tag!r}")

    config = args.config.read_text()
    for placeholder, tag in replacements.items():
        marker = f'tag = "{placeholder}"'
        if config.count(marker) != 1:
            raise SystemExit(f"Expected exactly one cache placeholder: {placeholder}")
        config = config.replace(marker, f'tag = "{tag}"')
    args.config.write_text(config)
    print(f"Configured isolated Docker cache tag: {args.docker_tag}")
    print(f"Configured isolated ccache tag: {args.ccache_tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
