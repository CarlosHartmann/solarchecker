#!/bin/zsh

emulate -L zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
HISTORY_DIR="${REPO_ROOT}/history"

if [[ ! -d "${HISTORY_DIR}" ]]; then
  print -u2 "ERROR: history directory not found: ${HISTORY_DIR}"
  exit 1
fi

current_year="$(date +%Y)"
next_year="$((current_year + 1))"
remote_dir="${current_year}-Feb${next_year}"
remote_path="almazen:solardaten/${remote_dir}"

print "Target remote directory: ${remote_path}"
/usr/local/bin/rclone mkdir "${remote_path}"

if [[ -f "${HOME}/.zshrc" ]]; then
  source "${HOME}/.zshrc"
fi

if ! typeset -f rclone-custom >/dev/null 2>&1; then
  print -u2 "ERROR: rclone-custom function is not available after sourcing ~/.zshrc"
  exit 127
fi

gesamt_files=("${HISTORY_DIR}"/*gesamt*.xlsx(N))
if (( ${#gesamt_files} == 0 )); then
  print "No gesamt files found in ${HISTORY_DIR}; nothing to move."
  exit 0
fi

for file_path in "${gesamt_files[@]}"; do
  print "Moving ${file_path} -> ${remote_path}"
  rclone-custom move "${file_path}" "${remote_path}"
done

print "Upload job completed."
