#!/usr/bin/env bash
# Stylized, honest terminal demo of the llm-council deliberation pipeline.
# Rendered to docs/demo.gif via asciinema + agg. Not a live capture — it shows
# the real tool name (ask_council), real stage semantics (anonymized peer
# review, council confidence, chairman synthesis with [A]/[B] attribution) and
# real model ids, with one illustrative question and an illustrative ranking.
#
# Re-render:
#   asciinema rec -c "bash docs/demo.sh" --overwrite /tmp/llm-council-demo.cast
#   agg --no-loop --cols 92 --rows 28 --font-size 20 --line-height 1.4 \
#       --theme monokai /tmp/llm-council-demo.cast docs/demo.gif
set -u

# ---- palette ----
C_PROMPT='\033[38;5;81m'   # cyan prompt
C_CMD='\033[1m\033[97m'    # bold white command
C_DIM='\033[38;5;245m'     # dim description
C_OK='\033[38;5;114m'      # green
C_S1='\033[38;5;81m'       # cyan   - stage 1 (generate)
C_S2='\033[38;5;141m'      # purple - stage 2 (rank)
C_WARN='\033[38;5;221m'    # yellow - confidence
C_CHAIR='\033[38;5;213m'   # pink   - stage 3 (chairman)
R='\033[0m'

type_cmd() {  # animate typing a request after a prompt
  printf "${C_PROMPT}> ${R}"
  local s="$1" i
  for (( i=0; i<${#s}; i++ )); do printf "${C_CMD}%s${R}" "${s:$i:1}"; sleep 0.020; done
  printf '\n'; sleep 0.35
}
line() { printf "%b\n" "$1"; sleep 0.22; }
gap()  { printf '\n'; sleep 0.30; }

clear
sleep 0.4
line "${C_DIM}# ask a council of LLMs, not one model - Claude Code / Codex / OpenCode${R}"
printf "${C_PROMPT}\$ ${R}${C_CMD}claude${R}\n"; sleep 0.5
line "  ${C_OK}+${R} llm-council MCP ready  ${C_DIM}- provider=openrouter${R}"
gap

type_cmd 'ask the council: will Postgres LISTEN/NOTIFY survive a dropped conn?'
line "  ${C_DIM}ask_council - mode=auto ->${R} standard"
gap

line "  ${C_S1}STAGE 1${R}  ${C_DIM}fan-out -> 6 models answer in parallel, no cross-talk${R}"
line "           gpt-5.1 ${C_OK}+${R} claude-opus ${C_OK}+${R} gemini ${C_OK}+${R} grok ${C_OK}+${R} deepseek ${C_OK}+${R} qwen ${C_OK}+${R}"
gap

line "  ${C_S2}STAGE 2${R}  ${C_DIM}anonymized peer review - each ranks the rest as Response A-F${R}"
line "           ${C_DIM}names stripped, so no model favors its own brand${R}"
line "           ${C_DIM}aggregate:${R} B - D - A - F - C - E"
line "           ${C_WARN}confidence: SPLIT${R}  ${C_DIM}top-1 unstable -> chairman told to hedge${R}"
gap

line "  ${C_CHAIR}STAGE 3${R}  ${C_DIM}chairman synthesis - model from outside the council family${R}"
line "           ${C_DIM}-> one answer, [A]/[B] markers on every checkable claim${R}"
line "           ${C_OK}+${R} NOTIFY is dropped on disconnect - re-LISTEN and resync ${C_DIM}[B,D]${R}"
gap
sleep 0.3
line "  ${C_DIM}one model  -> one confident guess${R}"
line "  ${C_OK}a council -> answers ranked blind, then synthesized, disagreement shown${R}"
sleep 1.5
