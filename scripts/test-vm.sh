#!/usr/bin/env bash

set -uo pipefail

readonly ROOT_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

if ! command -v vagrant >/dev/null 2>&1; then
    printf 'FAIL: Host prerequisite: vagrant is not installed\n' >&2
    exit 1
fi

cd "${ROOT_DIRECTORY}" || exit 1
printf '%s\n' 'PASS: Host prerequisite: vagrant is available'
printf '%s\n' 'Running checks in the disposable development VM...'

# bash -s consumes the script on stdin; its exit status is returned by SSH.
vagrant ssh -c 'bash -s' <<'VM_CHECKS'
set -uo pipefail

passed=0
failed=0
skipped=0
cleanup_required=0

run_step() {
    local label=$1
    local status
    shift
    printf '\nSTEP: %s\n' "${label}"
    if "$@"; then
        passed=$((passed + 1))
        printf 'PASS: %s\n' "${label}"
        return 0
    else
        status=$?
        failed=$((failed + 1))
        printf 'FAIL: %s (exit %s)\n' "${label}" "${status}" >&2
        return 1
    fi
}

skip_step() {
    skipped=$((skipped + 1))
    printf 'SKIP: %s — %s\n' "$1" "$2"
}

check_json() {
    scripts/vm-run.sh "$@" | scripts/vm-run.sh python -c \
        'import json, sys; value = json.load(sys.stdin); assert isinstance(value, dict); print("Valid JSON object")'
}

check_connectivity() {
    local output status
    output=$(sudo -n mn --test pingall 2>&1)
    status=$?
    printf '%s\n' "${output}"
    # Mininet can exit successfully even when pingall reports packet loss.
    (( status == 0 )) && grep -Eq 'Results: 0% dropped' <<<"${output}"
}

cleanup_on_exit() {
    local status=$?
    if (( cleanup_required )); then
        if ! run_step 'Emergency cleanup' \
            sudo -n scripts/check-mininet-cleanup.sh recover; then
            status=1
        fi
    fi
    return "${status}"
}
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! run_step 'Project mount' cd /vagrant; then
    exit 1
fi

run_step 'Python and Mininet imports' scripts/vm-run.sh python -c \
    'import sys, mininet, mininet_ai; assert sys.version_info >= (3, 14); print(sys.version)'
run_step 'Shell script syntax' bash -n \
    scripts/vm-run.sh scripts/test-vm.sh scripts/check-mininet-cleanup.sh
run_step 'Rootless test suite' env MININET_AI_LIVE_TESTS=0 \
    scripts/vm-run.sh pytest -q
run_step 'Lint' scripts/vm-run.sh ruff check .
run_step 'Type checking' scripts/vm-run.sh pyright
run_step 'Example validation' scripts/vm-run.sh mininet-ai validate \
    examples/iperf-throughput/experiment.yaml
run_step 'Example deployment plan' check_json mininet-ai plan \
    examples/iperf-throughput/experiment.yaml --format json
run_step 'Example dry run' check_json mininet-ai run \
    examples/iperf-throughput/experiment.yaml --dry-run --format json
for schema in experiment agent-blueprint capability deployment-plan runtime-event \
    coordination-message coordination-outcome; do
    run_step "Schema: ${schema}" check_json mininet-ai schema "${schema}"
done

network_ready=1
run_step 'Passwordless sudo' sudo -n true || network_ready=0
run_step 'Network tools' bash -c \
    'for tool in mn ovs-vsctl ovs-ofctl ip tc grep; do command -v "$tool" || exit 1; done' \
    || network_ready=0
run_step 'Open vSwitch service' systemctl is-active --quiet openvswitch-switch \
    || network_ready=0
run_step 'Open vSwitch access' sudo -n ovs-vsctl --timeout=5 show \
    || network_ready=0

if (( network_ready )); then
    if run_step 'Initial Mininet cleanup' sudo -n mn -c; then
        if run_step 'Clean networking baseline' \
            sudo -n scripts/check-mininet-cleanup.sh snapshot --force; then
            cleanup_required=1
            run_step 'Live integration tests' sudo -n env MININET_AI_LIVE_TESTS=1 \
                scripts/vm-run.sh python -m unittest \
                tests.integration.test_mininet_ovs_runtime -v
            run_step 'Integration cleanup verification' \
                sudo -n scripts/check-mininet-cleanup.sh check
            # Restore the baseline even if integration tests left resources behind.
            if run_step 'Recovery before connectivity test' \
                sudo -n scripts/check-mininet-cleanup.sh recover; then
                run_step 'Mininet connectivity (pingall)' check_connectivity
            else
                skip_step 'Mininet connectivity (pingall)' 'recovery failed'
            fi
            if run_step 'Final cleanup and baseline verification' \
                sudo -n scripts/check-mininet-cleanup.sh recover; then
                cleanup_required=0
            fi
        else
            skip_step 'Live network tests' 'baseline could not be recorded'
        fi
    else
        skip_step 'Live network tests' 'initial cleanup failed'
    fi
else
    skip_step 'Live network tests' 'network prerequisites failed'
fi

# Make any final recovery attempt before summarizing its result.
if (( cleanup_required )); then
    run_step 'Emergency cleanup' sudo -n scripts/check-mininet-cleanup.sh recover
    cleanup_required=0
fi
printf '\nSUMMARY: %s passed, %s failed, %s skipped\n' \
    "${passed}" "${failed}" "${skipped}"
(( failed == 0 ))
VM_CHECKS
status=$?
if (( status != 0 )); then
    printf 'FAIL: VM verification (exit %s); see step results above\n' "${status}" >&2
else
    printf '%s\n' 'PASS: VM verification'
fi
exit "${status}"
