# bash completion for tmux-there, printed by `tmux-there completion bash`. It needs
# bash-completion, which loads it from ~/.local/share/bash-completion/completions/tmux-there.

# The names attach takes, into the caller's $names. The last full scan keeps them in a file, and
# a file younger than 5 seconds is read without starting Python. Anything older costs a scan,
# which writes the file again and gives up after 5 seconds.
_tmux_there_sessions() {
    local cache=${XDG_CACHE_HOME:-$HOME/.cache}/fprochazka-tmux-tools/there-sessions
    local mtime now
    if [[ -f $cache ]]; then
        # GNU stat first, then the BSD one macOS ships
        mtime=$(stat -c %Y -- "$cache" 2>/dev/null || stat -f %m -- "$cache" 2>/dev/null)
        printf -v now '%(%s)T' -1
        if [[ $mtime =~ ^[0-9]+$ ]] && ((now - mtime < 5)); then
            mapfile -t names <"$cache"
            return
        fi
    fi
    mapfile -t names < <(tmux-there ls --names 2>/dev/null)
}

_tmux_there() {
    local cur words cword
    _get_comp_words_by_ref -n =: cur words cword

    if ((cword == 1)); then
        mapfile -t COMPREPLY < <(compgen -W 'ls attach completion' -- "$cur")
        return
    fi
    if ((cword == 2)) && [[ ${words[1]} == completion ]]; then
        mapfile -t COMPREPLY < <(compgen -W 'bash zsh' -- "$cur")
        return
    fi
    if ((cword != 2)) || [[ ${words[1]} != attach ]]; then
        return
    fi

    # What was typed, without the shell quoting around it. Inside quotes readline puts the
    # names in as they are, outside them every name has to be escaped the way the shell reads it.
    local quote="" typed="" i
    case $cur in
        \'* | \"*)
            quote=${cur:0:1}
            typed=${cur:1}
            ;;
        *)
            for ((i = 0; i < ${#cur}; i++)); do
                [[ ${cur:i:1} == \\ ]] && ((i++))
                typed+=${cur:i:1}
            done
            ;;
    esac

    # readline replaces only the part of the word after its last = or :, as long as they stay
    # in COMP_WORDBREAKS. A name can then complete only when it starts with the whole word.
    local broken=""
    if [[ -z $quote && $cur == *[=:]* ]]; then
        broken=${cur%"${cur##*[=:]}"}
        [[ $COMP_WORDBREAKS == *"${broken: -1}"* ]] || broken=""
    fi

    local -a names matches=()
    _tmux_there_sessions
    local name
    for name in "${names[@]}"; do
        if [[ -n $broken ]]; then
            [[ $name == "$typed"* ]] || continue
        else
            [[ $name == *"$typed"* ]] || continue
        fi
        matches+=("$name")
    done

    COMPREPLY=()
    for name in "${matches[@]}"; do
        [[ -z $quote ]] && printf -v name %q "$name"
        COMPREPLY+=("${name:${#broken}}")
    done

    # With several matches readline replaces the word with their longest common prefix, which
    # need not contain what was typed: "op@" against acme/shop@attic and acme/stop@workshop
    # becomes "acme/s". An empty common prefix is the one case where readline keeps the word,
    # so one more entry that starts with a space makes it empty. It lists as the typed text,
    # and inserted by menu-complete it is the same word with an extra space before it.
    if ((${#matches[@]} > 1)) && [[ -z $broken ]]; then
        local common=${matches[0]}
        for name in "${matches[@]:1}"; do
            while [[ $name != "$common"* ]]; do
                common=${common%?}
            done
        done
        [[ -z $common || $common == *"$typed"* ]] || COMPREPLY+=(" $cur")
    fi
}

complete -F _tmux_there tmux-there
