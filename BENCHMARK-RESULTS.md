# Immich base-images cache benchmark

This benchmark compares four cache setups for Immich's server base images:

- GitHub Actions cache
- Immich's GHCR registry cache shape: `type=registry,mode=max,compression=zstd`
- BoringCache layer cache
- BoringCache layer cache with ccache and apt mount caches

The [primary workflow](https://github.com/boringcache/base-images/actions/runs/31700820652) ran on 13 August 2026. All 98 jobs passed: one configuration check, 48 seed builds, 48 rebuilds, and one report job.

## Result

Across the 12 changed-base rebuilds, BoringCache with ccache and apt mount caches used 74m23s of runner time. The GHCR control used 115m47s. That is a 36% reduction, and the BoringCache setup was faster in every paired test.

BoringCache layer cache alone used 113m07s, close to the GHCR result. The larger improvement came from keeping compiler and package-manager caches useful after the Docker base layer changed.

Final-image push was disabled for every setup. The timings include cache setup and import, the BuildKit build, and cache export.

## Rebuild times

Each result below is the median of three trials, followed by the full range.

| Target | Architecture | GHCR | BoringCache layers | BoringCache with ccache and apt caches | Change from GHCR |
| --- | --- | ---: | ---: | ---: | ---: |
| `dev` | AMD64 | 11m12s (11m04s–11m17s) | 10m38s (10m24s–10m57s) | 7m09s (6m46s–8m04s) | 36% less |
| `dev` | ARM64 | 7m45s (7m35s–7m53s) | 7m46s (7m40s–7m49s) | 5m28s (5m09s–5m37s) | 29% less |
| `prod` | AMD64 | 12m19s (9m52s–12m29s) | 11m17s (10m41s–11m49s) | 7m45s (6m01s–7m47s) | 37% less |
| `prod` | ARM64 | 8m16s (7m43s–8m22s) | 7m51s (7m34s–8m41s) | 4m58s (4m40s–4m59s) | 40% less |

Totals across all 12 rebuilds:

| Cache setup | Runner time | Change from GHCR |
| --- | ---: | ---: |
| GitHub Actions cache | 131m59s | 14% more |
| GHCR registry cache | 115m47s | baseline |
| BoringCache layers | 113m07s | 2% less |
| BoringCache with ccache and apt caches | 74m23s | 36% less |

The optimized setup also won each individual comparison. Its three complete matrix totals were 37%, 39%, and 30% lower than the matching GHCR totals.

## Test setup

- The source moved from commit `a645c0e` to `9163399`. The only change was Node 24.14.1 to 24.18.0 in the two `server/Dockerfile` base references.
- AMD64 ran on `ubuntu-latest`; ARM64 ran on `ubuntu-24.04-arm`. These are Immich's native multi-runner defaults.
- Every setup used Immich's pinned Buildx 4.1.0 and build-push 7.2.0 actions.
- Every trial had a separate cache scope for the run, repetition, setup, target, and architecture.
- The matrix ran at most 12 jobs at once to avoid unnecessary contention against upstream download hosts.
- BoringCache ran with `fail-on-cache-error: true`. It reported no cache errors.

An earlier attempt started all 48 seed jobs at once and one job encountered a connection reset while downloading public geodata from `raw.githubusercontent.com`. That run was cancelled and excluded. The completed workflow above used the 12-job limit.

## No-layer check

The [no-layer workflow](https://github.com/boringcache/base-images/actions/runs/31709427865) reused ccache and apt caches from the first optimized trial while disabling Docker layer reuse. All four builds completed with zero `CACHED` Docker steps.

| Target | Architecture | No layers, with ccache and apt caches | Full optimized setup | GHCR trial 1 |
| --- | --- | ---: | ---: | ---: |
| `dev` | AMD64 | 10m14s | 6m46s | 11m17s |
| `dev` | ARM64 | 5m56s | 5m09s | 7m45s |
| `prod` | AMD64 | 7m39s | 7m47s | 12m29s |
| `prod` | ARM64 | 5m56s | 4m58s | 7m43s |

The four no-layer builds took 29m45s in total, compared with 39m14s for the matching GHCR trial and 24m40s for the full optimized setup. The compiler and apt caches therefore remained useful when Docker could not reuse any layers, while reusable layers still saved another 5m05s.

All six expected apt caches restored successfully. ccache served 7,608 of 9,088 lookups, an 83.7% hit rate, with no errors. This was one seeded trial rather than three, so it supports the explanation of the main result but is not a separate performance distribution.
