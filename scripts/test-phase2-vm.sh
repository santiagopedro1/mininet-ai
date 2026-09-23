#!/usr/bin/env bash

set -euo pipefail

readonly ROOT_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

if ! command -v vagrant >/dev/null 2>&1; then
    printf 'ERROR: vagrant is not installed on the host\n' >&2
    exit 1
fi

cd "${ROOT_DIRECTORY}"

printf '%s\n' 'Running Phase 1 tests in the Phase 2 VM...'
vagrant ssh -c '
    set -eu
    cd /vagrant

    scripts/vm-run.sh python -c "import mininet, mininet_ai"
    scripts/vm-run.sh python -m unittest discover -v

    sudo -n true
    systemctl is-active --quiet openvswitch-switch
    sudo ovs-vsctl --timeout=5 show >/dev/null

    cleanup_required=0
    cleanup() {
        if [ "${cleanup_required}" -eq 1 ]; then
            sudo scripts/check-mininet-cleanup.sh recover || true
        fi
    }
    trap cleanup EXIT

    sudo mn -c
    sudo scripts/check-mininet-cleanup.sh snapshot --force
    cleanup_required=1
    sudo mn --test pingall
    sudo scripts/check-mininet-cleanup.sh recover
    cleanup_required=0
'

printf '%s\n' 'PASS: Phase 2 VM environment is ready.'
