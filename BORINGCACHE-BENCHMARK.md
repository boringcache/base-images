# BoringCache base-images benchmark

This branch keeps the experiment in the real `boringcache/base-images` fork while building immutable source commits from `immich-app/base-images`.

The completed benchmark and prospect brief are in [`BENCHMARK-RESULTS.md`](./BENCHMARK-RESULTS.md).

## Workload

- Seed: `a645c0ebfe59d53112d8773dca7936b5f90c684d`
- Rebuild: `9163399e7675da7e9087171a4ff2a49f815acc27`
- Upstream change: only the two Node base-image references in `server/Dockerfile`, from Node 24.14.1 to 24.18.0
- Targets: upstream `dev` and `prod`
- Architectures: native `linux/amd64` on `ubuntu-latest` and native `linux/arm64` on `ubuntu-24.04-arm`, matching Immich's multi-runner defaults

The Node base-image transition invalidates the native dependency graph without mixing in an Immich source-code change. Both phases use exact 40-character upstream SHAs.

## Lanes

1. **GitHub Actions** uses the untouched upstream Dockerfile with Buildx `type=gha,mode=max`. Buildx setup and build-action SHAs match the immutable Immich multi-runner recipe referenced by the rebuild commit.
2. **BoringCache layer cache** uses the untouched upstream Dockerfile with BoringCache's Docker layer cache.
3. **BoringCache optimized** adds ccache 4.13.6 compiler wrappers and real BuildKit apt cache mounts, then enables Docker layer cache, `docker-tool-cache: ccache`, and `docker-mount-cache: true`.

The optimized Dockerfile contains no cache credentials. BoringCache One injects session-only remote-cache configuration into BuildKit. The ccache release binaries for amd64 and arm64 are pinned by SHA-256.

Each workflow run receives isolated cache tags per provider, target, and architecture. A retry deliberately keeps the original workflow-run tags, allowing a transiently failed rebuild job to consume the seed produced by that run; starting a new workflow creates a fresh cache cohort. For BoringCache, the composite action replaces the checked-in Docker and ccache tag placeholders before invoking BoringCache One; Docker's image `tags` input is intentionally separate from that layer-cache identity. Seed and rebuild execute in separate jobs, so a rebuild must restore remote state rather than reuse a runner-local builder.

A preflight job verifies that the two source commits differ only by the Node image references and that the optimized profile still produces the expected ccache wrapper and four architecture-scoped apt package mounts. Apt metadata is deliberately not mounted because Immich's later `ffmpeg.sh` step consumes the package indexes written by the base stage.

The benchmark does not push final images. Its provider timing covers cache setup/import, the BuildKit solve, and cache export; registry image push time is deliberately out of scope.
