#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  cat <<'USAGE'
Usage: bash bootstrap_install.sh <repo_url> [branch]
Example:
  bash bootstrap_install.sh https://github.com/example/xdl-relay.git main
USAGE
  exit 1
fi

REPO_URL="$1"
BRANCH="${2:-main}"
WORKDIR="$(mktemp -d /tmp/xdl-relay-install-XXXXXX)"
REPO_DIR="${WORKDIR}/repo"

cleanup() {
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

if command -v git >/dev/null 2>&1; then
  :
else
  echo "git is required. Install git, then rerun this command."
  exit 1
fi

echo "Cloning ${REPO_URL} (${BRANCH})..."
git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${REPO_DIR}"

echo "Starting guided installer..."
(
  cd "${REPO_DIR}"
  bash "scripts/install_linux_service.sh"
)
