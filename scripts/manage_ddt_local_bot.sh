#!/bin/zsh
set -euo pipefail

readonly LABEL="com.vietlottai.ddt-local-bot"
readonly SELF_PATH="${0:A}"
readonly SCRIPT_DIR="${0:A:h}"
readonly PROJECT_ROOT="${SCRIPT_DIR:h}"
readonly TEMPLATE_PATH="${PROJECT_ROOT}/deploy/launchd/${LABEL}.plist.template"
readonly LAUNCH_AGENT_DIR="${HOME}/Library/LaunchAgents"
readonly PLIST_PATH="${LAUNCH_AGENT_DIR}/${LABEL}.plist"
readonly LOG_DIR="${DDT_LOG_DIR:-${PROJECT_ROOT}/.local/ddt-bot}"
readonly WAKE_MARKER="${LOG_DIR}/pmset-repeat-wake-20-55"
readonly GUI_DOMAIN="gui/$(id -u)"
readonly PMSET_BIN="${DDT_PMSET_BIN:-/usr/bin/pmset}"
readonly SUDO_BIN="${DDT_SUDO_BIN:-/usr/bin/sudo}"
readonly WAKE_DAYS="MTWRFSU"
readonly WAKE_TIME="20:55:00"

python_path() {
  if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    print -r -- "${PROJECT_ROOT}/.venv/bin/python"
  else
    command -v python3
  fi
}

render_plist() {
  local python_bin
  python_bin="$(python_path)"
  mkdir -p "${LAUNCH_AGENT_DIR}" "${LOG_DIR}"
  sed \
    -e "s|__PROJECT_ROOT__|${PROJECT_ROOT}|g" \
    -e "s|__PYTHON__|${python_bin}|g" \
    "${TEMPLATE_PATH}" > "${PLIST_PATH}"
  plutil -lint "${PLIST_PATH}"
}

install_agent() {
  render_plist
  launchctl bootout "${GUI_DOMAIN}/${LABEL}" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "${GUI_DOMAIN}" "${PLIST_PATH}"
  launchctl enable "${GUI_DOMAIN}/${LABEL}"
  print "Installed and started ${LABEL}"
}

start_agent() {
  render_plist
  launchctl bootout "${GUI_DOMAIN}/${LABEL}" 2>/dev/null || true
  sleep 1
  launchctl bootstrap "${GUI_DOMAIN}" "${PLIST_PATH}"
  launchctl enable "${GUI_DOMAIN}/${LABEL}"
  launchctl kickstart -k "${GUI_DOMAIN}/${LABEL}"
  print "Started ${LABEL}"
}

stop_agent() {
  launchctl bootout "${GUI_DOMAIN}/${LABEL}" 2>/dev/null || true
  print "Stopped ${LABEL}"
}

status_agent() {
  launchctl print "${GUI_DOMAIN}/${LABEL}"
}

repeat_schedule() {
  "${PMSET_BIN}" -g sched | awk '
    /^Repeating power events:/ { in_repeat = 1; next }
    /^Scheduled power events:/ { in_repeat = 0 }
    in_repeat && NF && tolower($0) !~ /^[[:space:]]*none/ { print }
  '
}

wake_status() {
  local schedule
  schedule="$(repeat_schedule)"
  if [[ -n "${schedule}" ]]; then
    print -r -- "${schedule}"
  else
    print "No repeating power events configured."
  fi
  if [[ -f "${WAKE_MARKER}" ]]; then
    print "DDT wake marker: ${WAKE_MARKER}"
  else
    print "DDT wake marker: not installed"
  fi
}

wake_schedule_matches_ddt() {
  local schedule="$1"
  local normalized="${schedule:l}"
  local line_count
  line_count="$(print -r -- "${normalized}" | awk 'NF { count++ } END { print count + 0 }')"
  [[ "${line_count}" == "1" ]] \
    && [[ "${normalized}" == *"wakepoweron"* || "${normalized}" == *"wakeorpoweron"* ]] \
    && [[ "${normalized}" == *"20:55"* || "${normalized}" == *"8:55pm"* || "${normalized}" == *"8:55 pm"* ]] \
    && [[ "${normalized}" == *"every day"* || "${normalized}" == *"mtwrfsu"* ]]
}

install_wake_privileged() {
  local schedule
  schedule="$(repeat_schedule)"
  if [[ -n "${schedule}" ]]; then
    print "Refusing to overwrite a repeating power schedule changed during authorization:" >&2
    print -r -- "${schedule}" >&2
    return 3
  fi
  "${PMSET_BIN}" repeat wakeorpoweron "${WAKE_DAYS}" "${WAKE_TIME}"
}

install_wake() {
  local schedule installed
  mkdir -p "${LOG_DIR}"
  schedule="$(repeat_schedule)"
  if [[ -n "${schedule}" ]]; then
    if wake_schedule_matches_ddt "${schedule}"; then
      if [[ -f "${WAKE_MARKER}" ]]; then
        print "DDT repeating wake is already installed."
      else
        : > "${WAKE_MARKER}"
        print "Adopted the exact DDT repeating wake schedule."
      fi
      print -r -- "${schedule}"
      return 0
    fi
    print "Refusing to overwrite an existing repeating power schedule:" >&2
    print -r -- "${schedule}" >&2
    print "Review it with '${SELF_PATH} wake-status' before making any manual change." >&2
    return 3
  fi
  "${SUDO_BIN}" "${SELF_PATH}" _wake-install-privileged
  installed="$(repeat_schedule)"
  if ! wake_schedule_matches_ddt "${installed}"; then
    print "Wake command returned success but the exact DDT schedule was not found:" >&2
    print -r -- "${installed:-<empty>}" >&2
    return 4
  fi
  : > "${WAKE_MARKER}"
  print "Installed DDT repeating wake at ${WAKE_TIME} every day."
}

case "${1:-status}" in
  install) install_agent ;;
  start) start_agent ;;
  stop) stop_agent ;;
  restart)
    stop_agent
    start_agent
    ;;
  status) status_agent ;;
  wake-status) wake_status ;;
  wake-install) install_wake ;;
  _wake-install-privileged) install_wake_privileged ;;
  *)
    print "Usage: $0 {install|start|stop|restart|status|wake-status|wake-install}" >&2
    exit 2
    ;;
esac
