# bash completion for lbctl
#
# Install (pick one):
#   source /path/to/completions/lbctl.bash            # this shell only
#   cp completions/lbctl.bash /etc/bash_completion.d/lbctl   # system-wide
#   # Homebrew bash-completion@2 (macOS):
#   cp completions/lbctl.bash "$(brew --prefix)/etc/bash_completion.d/lbctl"
#
# Requires bash 3.2+ (macOS system bash is fine, no bash-completion package
# needed -- this file is self-contained).
#
# Dynamic completion of --partition/--pool/--member values shells out to
# `lbctl __complete ...` (a hidden subcommand, see lbctl itself), which
# talks to the real BIG-IP using whatever --host/--user/--password/--config/
# --no-verify/--partition/--pool you've already typed on the line (falling
# back to your config file, same as any other lbctl command). That means:
#   - Tab-completing --partition/--pool/--member can take a moment (one
#     REST call), and silently offers nothing if the BIG-IP is slow,
#     unreachable, or credentials aren't resolvable yet -- it never blocks
#     for a password prompt or throws an error into your terminal.
#   - It only runs when you're actually completing one of those three
#     flags' values, not on every Tab press.

_lbctl_commands="status disable enable list-partitions partitions list-pools pools list-members members"

_lbctl_common_opts="--host -H --user -u --password -c --config -i --init-config --no-verify --verbose -v --partition -p --pool -P --member -m -h --help"

_lbctl_subcommand_opts() {
    case "$1" in
        disable|enable)
            echo "--max-wait --poll --force --dry-run -n"
            ;;
        list-pools|pools)
            echo "--all-partitions -A --with-members"
            ;;
    esac
}

# Scan the already-typed command line for connection/context flags and
# print them back out (flag + value pairs) so a `lbctl __complete ...`
# call can reuse the same --host/--user/--password/--config/--no-verify/
# --partition/--pool the user has already given, instead of guessing.
_lbctl_forward_args() {
    local words=("$@")
    local i=0 n=${#words[@]}
    local out=()
    while [ "$i" -lt "$n" ]; do
        case "${words[$i]}" in
            --host|-H|--user|-u|--password|-c|--config|--partition|-p|--pool|-P)
                # Only forward this flag if its value is also present --
                # the word currently being typed (the last word on the
                # line) is deliberately excluded by the caller, so a flag
                # with no value here means its value hasn't been typed
                # yet and there's nothing usable to forward.
                if [ "$((i + 1))" -lt "$n" ]; then
                    out+=("${words[$i]}" "${words[$((i + 1))]}")
                    i=$((i + 1))
                fi
                ;;
            --no-verify)
                out+=("--no-verify")
                ;;
        esac
        i=$((i + 1))
    done
    if [ "${#out[@]}" -gt 0 ]; then
        printf '%s\n' "${out[@]}"
    fi
}

_lbctl_complete_dynamic() {
    # $1 = kind (partitions|pools|members), remaining = extra args
    local kind="$1"
    shift
    local forwarded=()
    while IFS= read -r line; do
        forwarded+=("$line")
    done < <(_lbctl_forward_args "${COMP_WORDS[@]:1:COMP_CWORD-1}")
    lbctl __complete "$kind" "${forwarded[@]}" "$@" 2>/dev/null
}

_lbctl() {
    local cur prev words cword
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    # Find the subcommand, if one has been typed already (the first word
    # after "lbctl" that isn't itself an option or an option's value).
    local subcommand=""
    local i=1
    while [ "$i" -lt "$COMP_CWORD" ]; do
        case "${COMP_WORDS[$i]}" in
            --host|-H|--user|-u|--password|-c|--config|--partition|-p|--pool|-P|--member|-m|--max-wait|--poll)
                i=$((i + 1))  # skip this flag's value too
                ;;
            -*)
                ;;
            *)
                subcommand="${COMP_WORDS[$i]}"
                break
                ;;
        esac
        i=$((i + 1))
    done

    case "$prev" in
        --partition|-p)
            COMPREPLY=($(compgen -W "$(_lbctl_complete_dynamic partitions)" -- "$cur"))
            return
            ;;
        --pool|-P)
            local extra=()
            if [ "$subcommand" = "list-pools" ] || [ "$subcommand" = "pools" ]; then
                extra=(--all-partitions)
            fi
            COMPREPLY=($(compgen -W "$(_lbctl_complete_dynamic pools "${extra[@]}")" -- "$cur"))
            return
            ;;
        --member|-m)
            COMPREPLY=($(compgen -W "$(_lbctl_complete_dynamic members)" -- "$cur"))
            return
            ;;
        --config|-c)
            COMPREPLY=($(compgen -f -- "$cur"))
            return
            ;;
        --host|-H|--user|-u|--password|--max-wait|--poll)
            return  # freeform value, nothing to suggest
            ;;
    esac

    if [ -z "$subcommand" ]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=($(compgen -W "$_lbctl_common_opts" -- "$cur"))
        else
            COMPREPLY=($(compgen -W "$_lbctl_commands" -- "$cur"))
        fi
        return
    fi

    if [[ "$cur" == -* ]]; then
        local opts="$_lbctl_common_opts $(_lbctl_subcommand_opts "$subcommand")"
        COMPREPLY=($(compgen -W "$opts" -- "$cur"))
    fi
}

complete -F _lbctl lbctl
