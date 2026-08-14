#!/usr/bin/env bash
set -euo pipefail

architecture="${1:?usage: stage-ccache-binary.sh ARCH DESTINATION}"
destination="${2:?usage: stage-ccache-binary.sh ARCH DESTINATION}"
version="4.13.6"

case "$architecture" in
  amd64)
    release_arch="x86_64"
    checksum="567b1b648411819590f918f045218c92da14418bdec3b30db94a3b4f5d77cf13"
    ;;
  arm64)
    release_arch="aarch64"
    checksum="fae67fb810e1f0d390409af6603355483572229e19183e68574cd0f851a6fb98"
    ;;
  *)
    echo "Unsupported architecture: $architecture" >&2
    exit 2
    ;;
esac

archive="ccache-${version}-linux-${release_arch}-glibc.tar.gz"
temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT

curl --fail --location --show-error --silent \
  --retry 8 --retry-all-errors --connect-timeout 30 --max-time 180 \
  "https://github.com/ccache/ccache/releases/download/v${version}/${archive}" \
  --output "${temporary_dir}/${archive}"

actual_checksum="$(sha256sum "${temporary_dir}/${archive}" | awk '{print $1}')"
if [[ "$actual_checksum" != "$checksum" ]]; then
  echo "Checksum mismatch for $archive" >&2
  exit 1
fi

tar xzf "${temporary_dir}/${archive}" -C "$temporary_dir"
install -m 0755 \
  "${temporary_dir}/ccache-${version}-linux-${release_arch}-glibc/ccache" \
  "$destination"
echo "Staged ccache ${version} for ${architecture}."
