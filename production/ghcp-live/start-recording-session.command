#!/bin/bash
# Launch the real interactive CLI. This does not record the screen or answer prompts.
set -euo pipefail
umask 077

root="$(cd "$(dirname "$0")/../.." && pwd -P)"
workspace="$HOME/ghcp-cli-demo-workspace"
copilot_bin=/opt/homebrew/bin/copilot

# Do not inherit blanket or assisted approval into a human-approval demonstration.
for name in COPILOT_ALLOW_ALL COPILOT_ASSISTED_APPROVAL; do
  case "${!name:-}" in
    ''|0|false) ;;
    *) printf 'Stop: %s is set. Review the demo permission environment before filming.\n' "$name" >&2; exit 1 ;;
  esac
done

[[ -x "$copilot_bin" ]] || { printf 'Copilot CLI not found: %s\n' "$copilot_bin" >&2; exit 1; }
[[ -f "$workspace/change-request.md" && -f "$workspace/src/server.mjs" ]] || {
  printf 'Prepared demo workspace is missing.\n' >&2
  exit 1
}
for role in cxo-server-analysis cxo-ui-analysis cxo-review; do
  [[ -f "$workspace/.github/agents/$role.agent.md" ]] || {
    printf 'Prepared agent is missing: %s\n' "$role" >&2
    exit 1
  }
done

mkdir -p "$root/evidence/ghcp-live/raw"
take="$(mktemp -d "$root/evidence/ghcp-live/raw/take.XXXXXX")"
mkdir "$take/logs"
printf 'GitHub Copilot CLI · live demo\nWorkspace: %s\nPrivate transcript: %s\n' "$workspace" "$take"
printf 'Screen recording is NOT started by this launcher.\n'
printf 'Handle trust/login yourself; then record the selected Terminal area before sending the investigation request.\n'
printf 'Inspect the plan and answer permissions yourself. No input is sent automatically.\n\n'

cd "$workspace"
exec /usr/bin/script -q "$take/terminal.log" "$copilot_bin" \
  --mode plan --no-auto-update --no-remote --no-remote-export \
  --disable-builtin-mcps --log-dir "$take/logs" --log-level info \
  --usage-output-file "$take/usage.json"
