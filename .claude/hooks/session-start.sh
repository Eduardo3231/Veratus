#!/bin/bash
# Prepara cada sessão do Claude Code na nuvem: instala os agentes da The Agency
# (versão fixada) e as dependências de teste. Idempotente; fora da nuvem não faz nada.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

AGENCY_REPO="https://github.com/msitarzewski/agency-agents"
AGENCY_COMMIT="652b2692f8d8ec2b1f1a38e559073e287dfdc469"
AGENTS_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agents"
MARKER="$AGENTS_DIR/.agency-agents-$AGENCY_COMMIT"
VENV="${VERATUS_VENV:-$HOME/.venvs/veratus}"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

# Encadeado com && porque, chamada à esquerda de ||, a função roda sem set -e.
install_agents() {
  local tmp status=1
  tmp="$(mktemp -d)"
  if git clone --quiet --filter=blob:none "$AGENCY_REPO" "$tmp/agency-agents" &&
    git -C "$tmp/agency-agents" checkout --quiet "$AGENCY_COMMIT" &&
    (cd "$tmp/agency-agents" && ./scripts/install.sh --tool claude-code --no-interactive >/dev/null); then
    touch "$MARKER"
    status=0
  fi
  rm -rf "$tmp"
  return "$status"
}

# Sem rede para o GitHub, a sessão segue sem os agentes em vez de não abrir.
if [ ! -f "$MARKER" ]; then
  install_agents || echo "session-start: agentes da The Agency não instalados" >&2
fi

# Voz da Veratus para as skills ig-* (leem ~/.claude/instagram/voice.md).
IG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/instagram"
mkdir -p "$IG_DIR"
[ -e "$IG_DIR/voice.md" ] || ln -s "$PROJECT_DIR/social/instagram/voice.md" "$IG_DIR/voice.md"

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$PROJECT_DIR/requirements-dev.txt"

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$VENV/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
