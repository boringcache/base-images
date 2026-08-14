#!/usr/bin/env python3
"""Fail if the pinned upstream transition or cache profile drifts."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


SEED_SHA = "a645c0ebfe59d53112d8773dca7936b5f90c684d"
REBUILD_SHA = "9163399e7675da7e9087171a4ff2a49f815acc27"
OLD_IMAGE = (
    "node:24.14.1-trixie-slim@"
    "sha256:9707cd4542f400df5078df04f9652a272429112f15202d22b5b8bdd148df494f"
)
NEW_IMAGE = (
    "node:24.18.0-trixie-slim@"
    "sha256:ae91dcc111a68c9d2d81ff2a17bda61be126426176fde6fe7d08ab13b7f50573"
)
UPSTREAM_BUILDX_ACTION = "d7f5e7f509e45cec5c76c4d5afdd7de93d0b3df5"
UPSTREAM_BUILD_PUSH_ACTION = "f9f3042f7e2789586610d6e8b85c8f03e5195baf"


def git(*args: str) -> str:
    return subprocess.check_output(("git", *args), text=True).strip()


def main() -> int:
    changed = git("diff", "--name-only", SEED_SHA, REBUILD_SHA).splitlines()
    if changed != ["server/Dockerfile"]:
        raise SystemExit(f"Pinned transition changed unexpected paths: {changed}")

    seed = git("show", f"{SEED_SHA}:server/Dockerfile")
    rebuild = git("show", f"{REBUILD_SHA}:server/Dockerfile")
    if seed.count(OLD_IMAGE) != 2 or rebuild.count(NEW_IMAGE) != 2:
        raise SystemExit("Expected exactly two pinned Node image references per commit")
    if seed.replace(OLD_IMAGE, NEW_IMAGE) != rebuild:
        raise SystemExit("Pinned transition contains more than the Node image update")

    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary)
        (source / "server").mkdir()
        dockerfile = source / "server/Dockerfile"
        dockerfile.write_text(rebuild + "\n")
        subprocess.run(
            (
                sys.executable,
                "scripts/prepare-benchmark-source.py",
                "ccache-mountcache",
                str(source),
            ),
            check=True,
        )
        optimized = dockerfile.read_text()
        if optimized.count("--mount=type=cache") != 4:
            raise SystemExit("Optimized profile must expose exactly four apt mounts")
        if "target=/var/lib/apt" in optimized:
            raise SystemExit(
                "Apt metadata must remain in the image for later ffmpeg install"
            )
        if 'ENV PATH="/usr/lib/ccache:${PATH}"' not in optimized:
            raise SystemExit(
                "Optimized profile does not select Debian's ccache wrappers"
            )
        if (
            "CCACHE_REMOTE_STORAGE" in optimized
            or "BORINGCACHE_RESTORE_TOKEN" in optimized
        ):
            raise SystemExit("Cache credentials must stay outside the Dockerfile")

        config = source / ".boringcache.toml"
        config.write_text(Path(".boringcache.toml").read_text())
        subprocess.run(
            (
                sys.executable,
                "scripts/configure-benchmark-cache-scope.py",
                str(config),
                "--docker-tag",
                "benchmark-dev-amd64-docker",
                "--ccache-tag",
                "benchmark-dev-amd64-ccache",
            ),
            check=True,
        )
        configured = config.read_text()
        if configured.count('tag = "benchmark-dev-amd64-docker"') != 1:
            raise SystemExit("Docker layer-cache scope was not configured")
        if configured.count('tag = "benchmark-dev-amd64-ccache"') != 1:
            raise SystemExit("ccache scope was not configured")

    action = Path(".github/actions/base-images-cache-benchmark/action.yml").read_text()
    if "configure-benchmark-cache-scope.py" not in action:
        raise SystemExit(
            "Composite action does not configure isolated BoringCache scopes"
        )
    if "GITHUB_RUN_ATTEMPT" in action:
        raise SystemExit("Retries must preserve the workflow run's seed cache cohort")
    expected_cohort = (
        'cohort="immich-base-r${GITHUB_RUN_ID}-n${REPETITION}-${TARGET}-'
        '${ARCHITECTURE}-${STRATEGY}"'
    )
    if expected_cohort not in action:
        raise SystemExit(
            "Cache cohort must be isolated by run, target, arch, and strategy"
        )
    if f"docker/setup-buildx-action@{UPSTREAM_BUILDX_ACTION}" not in action:
        raise SystemExit("Buildx setup must match Immich's pinned multi-runner recipe")
    if f"docker/build-push-action@{UPSTREAM_BUILD_PUSH_ACTION}" not in action:
        raise SystemExit("GHA baseline must match Immich's pinned multi-runner recipe")
    registry_cache = (
        "cache-to: type=registry,ref=${{ steps.scope.outputs.registry_cache_ref }},"
        "mode=max,compression=zstd"
    )
    if registry_cache not in action:
        raise SystemExit(
            "GHCR control must match Immich's max-mode zstd registry cache"
        )
    if action.count("push: false") != 3 or action.count("load: false") != 3:
        raise SystemExit("Every cache lane must disable final-image export")
    if "docker/login-action@650006c6eb7dba73a995cc03b0b2d7f5ca915bee" not in action:
        raise SystemExit("GHCR login must match Immich's pinned image-build recipe")
    if "no-cache: ${{ inputs.strategy == 'boringcache-no-layers' }}" not in action:
        raise SystemExit("No-layer lane must use the released Action's no-cache input")
    if action.count("inputs.strategy == 'boringcache-no-layers'") < 4:
        raise SystemExit("No-layer lane must retain the ccache and mount-cache profile")

    workflow = Path(
        ".github/workflows/boringcache-base-images-benchmark.yml"
    ).read_text()
    if "strategy: registry" not in workflow:
        raise SystemExit("Benchmark matrix does not include the GHCR registry control")
    if (
        "repeat: [1, 2, 3]" not in workflow
        or "repetition: ${{ matrix.repeat }}" not in workflow
    ):
        raise SystemExit("Benchmark workflow does not expose three isolated trials")
    if "max-parallel: 12" not in workflow:
        raise SystemExit("Benchmark must retain the proven single-trial fan-out")
    if "packages: write" not in workflow:
        raise SystemExit("Benchmark workflow cannot publish its isolated GHCR cache")

    no_layer_workflow = Path(
        ".github/workflows/boringcache-base-images-no-layer.yml"
    ).read_text()
    if 'SEEDED_CACHE_RUN_ID: "31700820652"' not in no_layer_workflow:
        raise SystemExit("No-layer ablation must pin the reviewed seeded cohort")
    if "strategy: boringcache-no-layers" not in no_layer_workflow:
        raise SystemExit("No-layer ablation does not select the no-cache strategy")
    if "max-parallel: 4" not in no_layer_workflow:
        raise SystemExit("No-layer ablation must run only the four requested cells")

    print(f"Verified upstream-only Node transition {SEED_SHA[:7]} -> {REBUILD_SHA[:7]}")
    print("Verified upstream action pins and retry-safe cache scopes")
    print("Verified upstream-style GHCR control and equal final-image export policy")
    print("Verified independently scoped three-trial matrix")
    print("Verified optimized ccache and mount-cache profile")
    print("Verified focused seeded no-layer ablation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
