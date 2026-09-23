#!/usr/bin/env bash

set -euo pipefail

readonly PROJECT_ROOT="/vagrant"
readonly VM_ENVIRONMENT="/home/vagrant/.venvs/mininet-ai"
readonly UV_EXECUTABLE="/usr/local/bin/uv"

if [[ ! -f "${PROJECT_ROOT}/pyproject.toml" ]]; then
    printf 'ERROR: Mininet AI is not mounted at %s\n' "${PROJECT_ROOT}" >&2
    exit 1
fi

if [[ ! -x "${UV_EXECUTABLE}" ]]; then
    printf 'ERROR: uv is not installed; run `vagrant provision`\n' >&2
    exit 1
fi

if [[ ! -x "${VM_ENVIRONMENT}/bin/python" ]]; then
    printf 'ERROR: VM environment is missing; run `vagrant provision`\n' >&2
    exit 1
fi

export PATH="${VM_ENVIRONMENT}/bin:/usr/local/bin:/usr/bin:/bin"
export UV_CACHE_DIR="/home/vagrant/.cache/uv"
export UV_PROJECT_ENVIRONMENT="${VM_ENVIRONMENT}"
export VIRTUAL_ENV="${VM_ENVIRONMENT}"

exec "${UV_EXECUTABLE}" run \
    --active \
    --frozen \
    --project "${PROJECT_ROOT}" \
    "$@"
