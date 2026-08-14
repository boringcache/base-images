#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
from pathlib import Path
from typing import Any


FULL_PROVIDERS = {
    "gha": "GitHub Actions",
    "registry": "Upstream-style GHCR registry",
    "boringcache": "BoringCache layer cache",
    "boringcache-optimized": "BoringCache + ccache + mount cache",
}
NO_LAYER_STRATEGY = "boringcache-no-layers"
PROVIDERS = {
    **FULL_PROVIDERS,
    NO_LAYER_STRATEGY: "BoringCache + ccache + mount cache, no layer reuse",
}
REGISTRY_BASELINE = "registry"
NO_LAYER_REFERENCE_RUN = "31700820652"
NO_LAYER_REFERENCE_REPETITION = 1
NO_LAYER_REFERENCE = {
    ("dev", "amd64"): {"optimized": 406, "registry": 677},
    ("dev", "arm64"): {"optimized": 309, "registry": 465},
    ("prod", "amd64"): {"optimized": 467, "registry": 749},
    ("prod", "arm64"): {"optimized": 298, "registry": 463},
}


def optional_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def phase(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_name = None
    if args.evidence and Path(args.evidence).is_file():
        evidence_name = (
            f"evidence-r{args.repetition}-{args.strategy}-{args.architecture}-"
            f"{args.target}-{args.phase}.json"
        )
        shutil.copy2(args.evidence, output_dir / evidence_name)

    payload = {
        "schema_version": 2,
        "benchmark": "immich-base-images",
        "strategy": args.strategy,
        "provider": PROVIDERS[args.strategy],
        "phase": args.phase,
        "architecture": args.architecture,
        "platform": args.platform,
        "target": args.target,
        "repetition": args.repetition,
        "profile": args.profile,
        "source_repository": "immich-app/base-images",
        "source_sha": args.source_sha,
        "timing": {
            "provider_seconds": args.provider_seconds,
            "workflow_seconds": args.workflow_seconds,
        },
        "cache": {
            "tag": args.cache_tag or None,
            "workspace": args.workspace or None,
            "hit": optional_bool(args.cache_hit),
            "import_ready": optional_bool(args.cache_import_ready),
            "import_refs": len(
                [line for line in args.cache_import_refs.splitlines() if line.strip()]
            ),
        },
        "docker": {"no_cache": optional_bool(args.no_cache)},
        "seeded_cache": {
            "run_id": args.cache_source_run_id or None,
            "repetition": (
                int(args.cache_source_repetition)
                if args.cache_source_repetition
                else None
            ),
        },
        "action_evidence": evidence_name,
        "github": {
            "repository": os.environ.get("GITHUB_REPOSITORY"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "job": os.environ.get("GITHUB_JOB"),
            "ref_name": os.environ.get("GITHUB_REF_NAME"),
        },
    }
    path = output_dir / (
        f"phase-r{args.repetition}-{args.strategy}-{args.architecture}-"
        f"{args.target}-{args.phase}.json"
    )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(path)
    return 0


def duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "n/a"
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def delta(baseline: int | float | None, candidate: int | float | None) -> str:
    if baseline is None or candidate is None or baseline == 0:
        return "n/a"
    percent = (baseline - candidate) / baseline * 100
    return f"{abs(percent):.0f}% {'faster' if percent >= 0 else 'slower'}"


def median(values: list[int]) -> int | float | None:
    return statistics.median(values) if values else None


def duration_distribution(values: list[int]) -> str:
    center = median(values)
    if center is None:
        return "n/a"
    if min(values) == max(values):
        return duration(center)
    return f"{duration(center)} ({duration(min(values))}–{duration(max(values))})"


def summarize(args: argparse.Namespace) -> int:
    phases: list[dict[str, Any]] = []
    for path in sorted(Path(args.input_dir).rglob("phase-*.json")):
        payload = json.loads(path.read_text())
        if payload.get("schema_version") in {1, 2}:
            payload.setdefault("repetition", 1)
            phases.append(payload)
    if not phases:
        raise SystemExit(f"No phase evidence found under {args.input_dir}")

    grouped: dict[tuple[str, str, str, int], dict[str, dict[str, Any]]] = {}
    for payload in phases:
        key = (
            payload["architecture"],
            payload["target"],
            payload["strategy"],
            payload["repetition"],
        )
        if payload["phase"] in grouped.setdefault(key, {}):
            raise SystemExit(f"Duplicate phase evidence for {key} {payload['phase']}")
        grouped.setdefault(key, {})[payload["phase"]] = payload

    expected_repetitions = range(1, args.expected_repetitions + 1)
    missing = [
        (architecture, target, strategy, repetition, phase)
        for target in ("dev", "prod")
        for architecture in ("amd64", "arm64")
        for strategy in FULL_PROVIDERS
        for repetition in expected_repetitions
        for phase in ("seed", "rebuild")
        if phase not in grouped.get((architecture, target, strategy, repetition), {})
    ]

    lines = [
        "## Immich base-images cache benchmark",
        "",
        "Exact upstream transition: `a645c0e` → `9163399` (Node 24.14.1 → 24.18.0).",
        f"Each value is the median and range across {args.expected_repetitions} independently scoped trial(s).",
        "Provider time includes cache setup/import, the BuildKit solve, and cache export. Final-image push is disabled in every lane.",
        "",
    ]
    aggregate: list[dict[str, Any]] = []
    for target in ("dev", "prod"):
        for architecture in ("amd64", "arm64"):
            registry_rebuilds = [
                grouped[(architecture, target, REGISTRY_BASELINE, repetition)][
                    "rebuild"
                ]["timing"]["provider_seconds"]
                for repetition in expected_repetitions
                if "rebuild"
                in grouped.get(
                    (architecture, target, REGISTRY_BASELINE, repetition), {}
                )
            ]
            gha_rebuilds = [
                grouped[(architecture, target, "gha", repetition)]["rebuild"]["timing"][
                    "provider_seconds"
                ]
                for repetition in expected_repetitions
                if "rebuild"
                in grouped.get((architecture, target, "gha", repetition), {})
            ]
            lines.extend(
                [
                    f"### `{target}` on `{architecture}`",
                    "",
                    "| Provider | Cold seed | Changed-base rebuild | vs GHCR | vs GHA | Samples |",
                    "| --- | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for strategy in FULL_PROVIDERS:
                seeds = [
                    grouped[(architecture, target, strategy, repetition)]["seed"][
                        "timing"
                    ]["provider_seconds"]
                    for repetition in expected_repetitions
                    if "seed"
                    in grouped.get((architecture, target, strategy, repetition), {})
                ]
                rebuilds = [
                    grouped[(architecture, target, strategy, repetition)]["rebuild"][
                        "timing"
                    ]["provider_seconds"]
                    for repetition in expected_repetitions
                    if "rebuild"
                    in grouped.get((architecture, target, strategy, repetition), {})
                ]
                rebuild_median = median(rebuilds)
                lines.append(
                    f"| {PROVIDERS[strategy]} | {duration_distribution(seeds)} "
                    f"| {duration_distribution(rebuilds)} "
                    f"| {delta(median(registry_rebuilds), rebuild_median)} "
                    f"| {delta(median(gha_rebuilds), rebuild_median)} "
                    f"| {len(rebuilds)} |"
                )
                aggregate.append(
                    {
                        "target": target,
                        "architecture": architecture,
                        "strategy": strategy,
                        "seed_seconds": seeds,
                        "seed_median_seconds": median(seeds),
                        "rebuild_seconds": rebuilds,
                        "rebuild_median_seconds": rebuild_median,
                        "vs_registry_rebuild": delta(
                            median(registry_rebuilds), rebuild_median
                        ),
                        "vs_gha_rebuild": delta(median(gha_rebuilds), rebuild_median),
                    }
                )
            lines.append("")

    rebuild_totals = {
        strategy: sum(
            payload["timing"]["provider_seconds"]
            for payload in phases
            if payload["strategy"] == strategy and payload["phase"] == "rebuild"
        )
        for strategy in FULL_PROVIDERS
    }
    registry_total = rebuild_totals[REGISTRY_BASELINE]
    lines.extend(
        [
            "### Aggregate rebuild runner time",
            "",
            "| Provider | Observations | Runner time | vs GHCR |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for strategy, provider in FULL_PROVIDERS.items():
        observations = sum(
            1
            for payload in phases
            if payload["strategy"] == strategy and payload["phase"] == "rebuild"
        )
        lines.append(
            f"| {provider} | {observations} | {duration(rebuild_totals[strategy])} "
            f"| {delta(registry_total, rebuild_totals[strategy])} |"
        )
    lines.append("")
    if missing:
        lines.extend(
            [
                "### Incomplete evidence",
                "",
                *[f"- Missing `{'/'.join(map(str, item))}`" for item in missing],
                "",
            ]
        )

    markdown = "\n".join(lines)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.md").write_text(markdown + "\n")
    (output_dir / "comparison.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "seed_sha": "a645c0ebfe59d53112d8773dca7936b5f90c684d",
                "rebuild_sha": "9163399e7675da7e9087171a4ff2a49f815acc27",
                "expected_repetitions": args.expected_repetitions,
                "complete": not missing,
                "results": aggregate,
                "rebuild_totals": rebuild_totals,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    print(output_dir / "comparison.md")
    return 1 if missing else 0


def summarize_no_layers(args: argparse.Namespace) -> int:
    phases: list[dict[str, Any]] = []
    for path in sorted(Path(args.input_dir).rglob("phase-*.json")):
        payload = json.loads(path.read_text())
        if (
            payload.get("schema_version") in {1, 2}
            and payload.get("strategy") == NO_LAYER_STRATEGY
            and payload.get("phase") == "rebuild"
        ):
            phases.append(payload)

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates: list[tuple[str, str]] = []
    for payload in phases:
        key = (payload["target"], payload["architecture"])
        if key in grouped:
            duplicates.append(key)
        grouped[key] = payload

    expected = [
        (target, architecture)
        for target in ("dev", "prod")
        for architecture in ("amd64", "arm64")
    ]
    missing = [key for key in expected if key not in grouped]
    invalid: list[str] = []
    for key, payload in grouped.items():
        if payload.get("docker", {}).get("no_cache") is not True:
            invalid.append(f"{key[0]}/{key[1]} did not record no-cache=true")
        if payload.get("profile") != "ccache-mountcache":
            invalid.append(f"{key[0]}/{key[1]} did not use the ccache/mount profile")
        seeded = payload.get("seeded_cache", {})
        if (
            str(seeded.get("run_id")) != NO_LAYER_REFERENCE_RUN
            or seeded.get("repetition") != NO_LAYER_REFERENCE_REPETITION
        ):
            invalid.append(f"{key[0]}/{key[1]} did not use the pinned seeded cohort")
        if payload.get("cache", {}).get("import_ready") is not True:
            invalid.append(f"{key[0]}/{key[1]} did not receive a readable cache import")

    lines = [
        "## Immich seeded no-layer ablation",
        "",
        "Exact upstream rebuild commit: `9163399`. Docker layer reuse is disabled with the released Action's `no-cache: true` input; ccache and apt mount cache reuse the optimized trial 1 cohort from workflow `31700820652`.",
        "Provider time includes BoringCache setup/import, the BuildKit solve, and cache export. Final-image push is disabled.",
        "",
        "| Target | Arch | No layers + ccache + mounts | Seeded optimized trial 1 | vs optimized | GHCR trial 1 | vs GHCR |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    rows: list[dict[str, Any]] = []
    for target, architecture in expected:
        payload = grouped.get((target, architecture))
        seconds = payload["timing"]["provider_seconds"] if payload else None
        reference = NO_LAYER_REFERENCE[(target, architecture)]
        lines.append(
            f"| `{target}` | {architecture.upper()} | {duration(seconds)} "
            f"| {duration(reference['optimized'])} "
            f"| {delta(reference['optimized'], seconds)} "
            f"| {duration(reference['registry'])} "
            f"| {delta(reference['registry'], seconds)} |"
        )
        rows.append(
            {
                "target": target,
                "architecture": architecture,
                "provider_seconds": seconds,
                "reference_optimized_seconds": reference["optimized"],
                "vs_reference_optimized": delta(reference["optimized"], seconds),
                "reference_registry_seconds": reference["registry"],
                "vs_reference_registry": delta(reference["registry"], seconds),
            }
        )

    total = sum(payload["timing"]["provider_seconds"] for payload in grouped.values())
    optimized_total = sum(row["optimized"] for row in NO_LAYER_REFERENCE.values())
    registry_total = sum(row["registry"] for row in NO_LAYER_REFERENCE.values())
    lines.extend(
        [
            "",
            "### Four-cell runner time",
            "",
            "| Lane | Runner time | Comparison |",
            "| --- | ---: | ---: |",
            f"| No layers + ccache + mounts | {duration(total)} | — |",
            f"| Seeded optimized trial 1 | {duration(optimized_total)} | {delta(optimized_total, total)} |",
            f"| GHCR trial 1 | {duration(registry_total)} | {delta(registry_total, total)} |",
            "",
        ]
    )
    if missing or duplicates or invalid:
        lines.extend(["### Invalid or incomplete evidence", ""])
        lines.extend(
            f"- Missing `{target}/{architecture}`" for target, architecture in missing
        )
        lines.extend(
            f"- Duplicate `{target}/{architecture}`"
            for target, architecture in duplicates
        )
        lines.extend(f"- {message}" for message in invalid)
        lines.append("")

    complete = not missing and not duplicates and not invalid
    markdown = "\n".join(lines)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "no-layer-comparison.md").write_text(markdown + "\n")
    (output_dir / "no-layer-comparison.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "complete": complete,
                "source_sha": "9163399e7675da7e9087171a4ff2a49f815acc27",
                "seeded_cache_run_id": NO_LAYER_REFERENCE_RUN,
                "seeded_cache_repetition": NO_LAYER_REFERENCE_REPETITION,
                "results": rows,
                "total_seconds": total,
                "reference_optimized_total_seconds": optimized_total,
                "reference_registry_total_seconds": registry_total,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    print(output_dir / "no-layer-comparison.md")
    return 0 if complete else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    write = commands.add_parser("phase")
    write.add_argument("--strategy", choices=PROVIDERS, required=True)
    write.add_argument("--phase", choices=("seed", "rebuild"), required=True)
    write.add_argument("--architecture", choices=("amd64", "arm64"), required=True)
    write.add_argument("--platform", required=True)
    write.add_argument("--target", choices=("dev", "prod"), required=True)
    write.add_argument("--repetition", type=int, required=True)
    write.add_argument("--profile", required=True)
    write.add_argument("--source-sha", required=True)
    write.add_argument("--provider-seconds", type=int, required=True)
    write.add_argument("--workflow-seconds", type=int, required=True)
    write.add_argument("--cache-tag", default="")
    write.add_argument("--workspace", default="")
    write.add_argument("--cache-hit", default="")
    write.add_argument("--cache-import-ready", default="")
    write.add_argument("--cache-import-refs", default="")
    write.add_argument("--no-cache", default="false")
    write.add_argument("--cache-source-run-id", default="")
    write.add_argument("--cache-source-repetition", default="")
    write.add_argument("--evidence", default="")
    write.add_argument("--output-dir", default="benchmark-results")

    report = commands.add_parser("summarize")
    report.add_argument("--input-dir", required=True)
    report.add_argument("--expected-repetitions", type=int, default=1)
    report.add_argument("--output-dir", default="benchmark-results")

    no_layers = commands.add_parser("summarize-no-layers")
    no_layers.add_argument("--input-dir", required=True)
    no_layers.add_argument("--output-dir", default="benchmark-results")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "phase":
        return phase(args)
    if args.command == "summarize-no-layers":
        return summarize_no_layers(args)
    return summarize(args)


if __name__ == "__main__":
    raise SystemExit(main())
