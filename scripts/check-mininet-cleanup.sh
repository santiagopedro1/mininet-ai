#!/usr/bin/env bash

set -u
set -o pipefail

readonly DEFAULT_BASELINE="/var/tmp/mininet-ai-cleanup.baseline"

usage() {
    cat <<'EOF'
Usage:
  sudo scripts/check-mininet-cleanup.sh snapshot [--baseline PATH] [--force]
  sudo scripts/check-mininet-cleanup.sh check [--baseline PATH]
  sudo scripts/check-mininet-cleanup.sh recover [--baseline PATH]

Commands:
  snapshot  Record the VM's clean networking state before an experiment.
  check     Fail if the current state differs from the recorded baseline.
  recover   Run Mininet's destructive cleanup (`mn -c`), then run check.

The captured state includes OVS bridges and ports, Linux network namespaces,
veth and Mininet-style interfaces, Linux bridges, qdiscs, Mininet/controller
processes, runtime registry files, and Mininet temporary files.

Run this script only inside the disposable Phase 2 VM. The recover command can
remove every Mininet/OVS topology on the machine, including one it did not
create.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_root() {
    if (( EUID != 0 )); then
        fail "run this command as root (for example, with sudo)"
    fi
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_inspection_commands() {
    local command_name
    for command_name in ovs-vsctl ip tc ps find sort diff; do
        require_command "${command_name}"
    done
}

verify_inspection_access() {
    ovs-vsctl --timeout=5 show >/dev/null 2>&1 ||
        fail "cannot read the Open vSwitch database"
    ip link show >/dev/null 2>&1 || fail "cannot inspect network links"
    ip netns list >/dev/null 2>&1 || fail "cannot inspect network namespaces"
    tc qdisc show >/dev/null 2>&1 || fail "cannot inspect traffic-control state"
}

emit_ovs_state() {
    local bridge port

    while IFS= read -r bridge; do
        [[ -n "${bridge}" ]] || continue
        printf 'ovs-bridge\t%s\n' "${bridge}"
        while IFS= read -r port; do
            [[ -n "${port}" ]] || continue
            printf 'ovs-port\t%s\t%s\n' "${bridge}" "${port}"
        done < <(ovs-vsctl --timeout=5 list-ports "${bridge}")
    done < <(ovs-vsctl --timeout=5 list-br)
}

emit_namespace_state() {
    local namespace

    while IFS= read -r namespace; do
        namespace=${namespace%% *}
        [[ -n "${namespace}" ]] || continue
        printf 'netns\t%s\n' "${namespace}"
    done < <(ip netns list)
}

emit_link_state() {
    local details interface

    while IFS= read -r details; do
        interface=${details#*: }
        interface=${interface%%:*}
        interface=${interface%%@*}
        [[ -n "${interface}" ]] || continue
        printf 'veth\t%s\n' "${interface}"
    done < <(ip -o link show type veth)

    while IFS= read -r details; do
        interface=${details#*: }
        interface=${interface%%:*}
        interface=${interface%%@*}
        [[ "${interface}" =~ -eth[0-9]+$ ]] || continue
        printf 'mininet-link\t%s\n' "${interface}"
    done < <(ip -o link show)

    while IFS= read -r details; do
        interface=${details#*: }
        interface=${interface%%:*}
        interface=${interface%%@*}
        [[ -n "${interface}" ]] || continue
        printf 'linux-bridge\t%s\n' "${interface}"
    done < <(ip -d -o link show type bridge)
}

emit_qdisc_state() {
    local qdisc

    while IFS= read -r qdisc; do
        [[ -n "${qdisc}" ]] || continue
        printf 'qdisc\t%s\n' "${qdisc}"
    done < <(tc qdisc show)
}

emit_process_state() {
    local args command_name pid

    while read -r pid command_name args; do
        case "${command_name}" in
            mn|mnexec|controller|ofprotocol|ofdatapath|ovs-controller|\
                ovs-openflowd|ovs-testcontroller|nox_core|lt-nox_core|\
                ryu-manager)
                printf 'process\t%s\t%s\t%s\n' "${pid}" "${command_name}" "${args}"
                ;;
            python|python3|python3.*)
                if [[ "${args}" =~ (^|[[:space:]])([^[:space:]]*/)?mn([[:space:]]|$) ]] ||
                    [[ "${args}" =~ (^|[[:space:]])-m[[:space:]]+mininet([[:space:]]|$) ]]; then
                    printf 'process\t%s\t%s\t%s\n' \
                        "${pid}" "${command_name}" "${args}"
                fi
                ;;
        esac
    done < <(ps -eo pid=,comm=,args=)
}

emit_runtime_file_state() {
    local path

    if [[ -d /run/mininet-ai ]]; then
        while IFS= read -r path; do
            printf 'runtime-file\t%s\n' "${path}"
        done < <(find /run/mininet-ai -mindepth 1 -print)
    fi

    while IFS= read -r path; do
        printf 'temporary-file\t%s\n' "${path}"
    done < <(
        find /tmp -maxdepth 1 \
            \( -name 'vconn*' -o -name 'vlogs*' -o -name '*.out' -o \
            -name '*.log' \) \
            -print
    )
}

capture_state() {
    {
        emit_ovs_state
        emit_namespace_state
        emit_link_state
        emit_qdisc_state
        emit_process_state
        emit_runtime_file_state
    } | LC_ALL=C sort -u
}

snapshot() {
    local destination=$1
    local force=$2
    local temporary

    if [[ -e "${destination}" && "${force}" != true ]]; then
        fail "baseline already exists: ${destination} (use --force to replace it)"
    fi

    temporary=$(mktemp "${destination}.tmp.XXXXXX") ||
        fail "could not create a temporary baseline next to ${destination}"

    if ! capture_state >"${temporary}"; then
        rm -f "${temporary}"
        fail "could not inspect the networking state"
    fi
    chmod 0600 "${temporary}"
    mv -f "${temporary}" "${destination}"

    printf 'Recorded clean baseline: %s\n' "${destination}"
}

check() {
    local baseline=$1
    local current difference

    [[ -f "${baseline}" ]] ||
        fail "baseline not found: ${baseline} (run snapshot first)"

    current=$(mktemp /tmp/mininet-ai-cleanup-current.XXXXXX) ||
        fail "could not create current-state file"
    difference=$(mktemp /tmp/mininet-ai-cleanup-diff.XXXXXX) || {
        rm -f "${current}"
        fail "could not create diff file"
    }

    if ! capture_state >"${current}"; then
        rm -f "${current}" "${difference}"
        fail "could not inspect the networking state"
    fi

    if diff -u "${baseline}" "${current}" >"${difference}"; then
        rm -f "${current}" "${difference}"
        printf 'PASS: networking state matches %s\n' "${baseline}"
        return 0
    fi

    printf 'FAIL: networking state differs from %s\n' "${baseline}" >&2
    printf '%s\n' 'Cleanup difference (expected baseline vs current state):' >&2
    cat "${difference}" >&2
    rm -f "${current}" "${difference}"
    return 1
}

clear_runtime_state() {
    local state_file=/run/mininet-ai/mininet-ovs.json

    if [[ -e "${state_file}" ]]; then
        unlink "${state_file}" || fail "could not remove ${state_file}"
    fi
    if [[ -d /run/mininet-ai ]]; then
        rmdir /run/mininet-ai ||
            fail "runtime state directory contains unexpected files"
    fi
}

main() {
    local action=${1:-}
    local baseline=${MININET_AI_CLEANUP_BASELINE:-${DEFAULT_BASELINE}}
    local cleanup_status=0
    local force=false

    case "${action}" in
        snapshot|check|recover)
            shift
            ;;
        -h|--help)
            usage
            return 0
            ;;
        '')
            usage >&2
            return 2
            ;;
        *)
            usage >&2
            fail "unknown command: ${action}"
            ;;
    esac

    while (( $# > 0 )); do
        case "$1" in
            --baseline)
                (( $# >= 2 )) || fail "--baseline requires a path"
                baseline=$2
                shift 2
                ;;
            --force)
                [[ "${action}" == snapshot ]] ||
                    fail "--force is valid only with snapshot"
                force=true
                shift
                ;;
            -h|--help)
                usage
                return 0
                ;;
            *)
                fail "unknown argument: $1"
                ;;
        esac
    done

    require_root
    require_inspection_commands
    verify_inspection_access

    case "${action}" in
        snapshot)
            snapshot "${baseline}" "${force}"
            ;;
        check)
            check "${baseline}"
            ;;
        recover)
            [[ -f "${baseline}" ]] ||
                fail "baseline not found: ${baseline} (run snapshot first)"
            require_command mn
            printf '%s\n' 'Running destructive Mininet cleanup: mn -c'
            mn -c || cleanup_status=$?
            clear_runtime_state
            check "${baseline}" || return 1
            if (( cleanup_status != 0 )); then
                fail "mn -c exited with status ${cleanup_status}"
            fi
            ;;
    esac
}

main "$@"
