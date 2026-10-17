# Spike: theme option B — per-pane background tint via OSC 11, driven by a zsh chpwd hook.
# Try it in a pane:  source spikes/05_osc_tint.zsh && cd ~/repos/cardak
typeset -gA CARDAK_TINT=(
  "$HOME/repos/cardak"   "#241b33"
  "$HOME/repos/webshop"  "#2b2410"
)

_cardak_tint() {
  local root bg=""
  for root in ${(k)CARDAK_TINT}; do
    [[ $PWD == $root || $PWD == $root/* ]] && bg=$CARDAK_TINT[$root]
  done
  if [[ -n $bg ]]; then
    printf '\e]11;%s\a' "$bg"   # set background
  else
    printf '\e]111\a'           # reset background to the scheme default
  fi
}

autoload -Uz add-zsh-hook
add-zsh-hook chpwd _cardak_tint
_cardak_tint
