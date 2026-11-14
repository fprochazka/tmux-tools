#compdef tmux-there

# zsh completion for tmux-there, printed by `tmux-there completion zsh`. Put it in a directory
# on fpath, before compinit runs, under the name _tmux-there.

# The names attach takes, into $reply. The last full scan keeps them in a file, and a file
# younger than 5 seconds is read without starting Python. Anything older costs a scan, which
# writes the file again and gives up after 5 seconds.
_tmux-there_sessions() {
  local cache=${XDG_CACHE_HOME:-$HOME/.cache}/fprochazka-tmux-tools/there-sessions
  local -a fresh
  fresh=( $cache(Nms-5) )
  if (( $#fresh )); then
    reply=( ${(f)"$(<$cache)"} )
  else
    reply=( ${(f)"$(tmux-there ls --names 2>/dev/null)"} )
  fi
}

_tmux-there() {
  local -a reply commands expl
  if (( CURRENT == 2 )); then
    commands=(
      'ls:list the sessions on every device'
      'attach:attach to session@device without asking'
      'completion:print the completion script for bash or zsh'
    )
    _describe -t commands 'tmux-there command' commands
  elif (( CURRENT == 3 )) && [[ $words[2] == attach ]]; then
    _tmux-there_sessions
    # The matcher lets the typed text match anywhere in the name, whatever matcher-list says
    _wanted sessions expl 'session@device' compadd -M 'l:|=* r:|=*' -a reply
  elif (( CURRENT == 3 )) && [[ $words[2] == completion ]]; then
    _wanted shells expl shell compadd bash zsh
  fi
}

# Autoloaded from fpath this file is the body of _tmux-there, and sourced it registers itself
if [[ $funcstack[1] == _tmux-there ]]; then
  _tmux-there "$@"
else
  compdef _tmux-there tmux-there
fi
