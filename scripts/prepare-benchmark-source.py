#!/usr/bin/env python3
"""Apply the benchmark-only ccache and apt mount-cache profile."""

from __future__ import annotations

import argparse
from pathlib import Path


class ProfileMismatch(RuntimeError):
    pass


def replace_once(source: str, before: str, after: str, boundary: str) -> str:
    count = source.count(before)
    if count != 1:
        raise ProfileMismatch(f"expected one {boundary}, found {count}")
    return source.replace(before, after, 1)


def replace_first_of_two(
    source: str, before: str, after: str, boundary: str
) -> str:
    count = source.count(before)
    if count != 2:
        raise ProfileMismatch(f"expected two {boundary} boundaries, found {count}")
    return source.replace(before, after, 1)


def render(source: str, profile: str) -> str:
    if profile == "upstream":
        return source
    if profile != "ccache-mountcache":
        raise ProfileMismatch(f"unknown cache profile: {profile}")

    if source.startswith("# syntax="):
        raise ProfileMismatch("upstream Dockerfile unexpectedly owns a syntax directive")
    source = "# syntax=docker/dockerfile:1.18\n\n" + source

    source = replace_once(
        source,
        "ARG DEBIAN_FRONTEND=noninteractive\n\nWORKDIR /usr/src/app\n",
        "ARG DEBIAN_FRONTEND=noninteractive\nARG TARGETPLATFORM\n\nWORKDIR /usr/src/app\n",
        "base-stage arguments",
    )
    source = replace_once(
        source,
        " AS prod\nARG DEBIAN_FRONTEND=noninteractive\n",
        " AS prod\nARG DEBIAN_FRONTEND=noninteractive\nARG TARGETPLATFORM\n",
        "prod-stage arguments",
    )
    source = replace_first_of_two(
        source,
        "RUN ./configure-apt.sh && \\\n",
        "RUN --mount=type=cache,id=apt-base-cache-${TARGETPLATFORM},target=/var/cache/apt,sharing=locked \\\n"
        "  rm -f /etc/apt/apt.conf.d/docker-clean && \\\n"
        "  ./configure-apt.sh && \\\n",
        "base apt bootstrap",
    )
    source = replace_first_of_two(
        source,
        "RUN . /etc/os-release && \\\n",
        "RUN --mount=type=cache,id=apt-base-cache-${TARGETPLATFORM},target=/var/cache/apt,sharing=locked \\\n"
        "  . /etc/os-release && \\\n",
        "base apt packages",
    )
    source = replace_once(
        source,
        "RUN ./configure-apt.sh && \\\n",
        "RUN --mount=type=cache,id=apt-prod-cache-${TARGETPLATFORM},target=/var/cache/apt,sharing=locked \\\n"
        "  rm -f /etc/apt/apt.conf.d/docker-clean && \\\n"
        "  ./configure-apt.sh && \\\n",
        "prod apt bootstrap",
    )
    source = replace_once(
        source,
        "RUN . /etc/os-release && \\\n",
        "RUN --mount=type=cache,id=apt-prod-cache-${TARGETPLATFORM},target=/var/cache/apt,sharing=locked \\\n"
        "  . /etc/os-release && \\\n",
        "prod apt packages",
    )
    source = replace_once(
        source,
        "  build-essential \\\n  cmake \\\n",
        "  build-essential \\\n  ccache \\\n  cmake \\\n",
        "base build package list",
    )
    source = replace_once(
        source,
        "  libaom-dev\n\nFROM base AS geodata\n",
        "  libaom-dev\n\n"
        "# BoringCache One v1.19.1 injects ccache's remote storage at build time.\n"
        "# Debian supplies the compiler wrappers; use the audited 4.13.6 binary.\n"
        "ARG CCACHE_VERSION=4.13.6\n"
        "COPY ccache /usr/bin/ccache\n"
        "RUN chmod 0755 /usr/bin/ccache && \\\n"
        "  ccache --version | grep -F \"ccache version ${CCACHE_VERSION}\"\n\n"
        'ENV PATH="/usr/lib/ccache:${PATH}"\n\n'
        "FROM base AS geodata\n",
        "base stage boundary",
    )
    source = replace_once(
        source,
        "  apt-get clean && \\\n",
        "",
        "prod apt cache cleanup",
    )
    source = replace_once(
        source,
        "  /var/lib/apt/lists \\\n",
        "",
        "prod apt lists cleanup",
    )

    if "CCACHE_REMOTE_STORAGE" in source or "BORINGCACHE_" in source.replace(
        "BoringCache One", ""
    ):
        raise ProfileMismatch("the Dockerfile must not own BoringCache credentials")
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=("upstream", "ccache-mountcache"))
    parser.add_argument("source", type=Path)
    args = parser.parse_args()

    dockerfile = args.source / "server/Dockerfile"
    rendered = render(dockerfile.read_text(), args.profile)
    dockerfile.write_text(rendered)
    print(f"Prepared {args.profile} profile at {dockerfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
