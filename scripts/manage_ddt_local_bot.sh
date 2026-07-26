#!/bin/zsh
set -euo pipefail

readonly LABEL="com.vietlottai.ddt-local-bot"
readonly SCRIPT_DIR="${0:A:h}"
readonly PROJECT_ROOT="${SCRIPT_DIR:h}"
readonly TEMPLATE_PATH="${PROJECT_ROOT}/deploy/launchd/${LABEL}.plist.template"
readonly LAUNCH_AGENT_DIR="${HOME}/Library/LaunchAgents"
readonly PLIST_PATH="${LAUNCH_AGENT_DIR}/${LABEL}.plist"
readonly LOG_DIR="${PROJECT_ROOT}/.local/ddt-bot"
readonly GUI_DOMAIN="gui/$(id -u)"

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

case "${1:-status}" in
  install) install_agent ;;
  start) start_agent ;;
  stop) stop_agent ;;
  restart)
    stop_agent
    start_agent
    ;;
  status) status_agent ;;
  *)
    print "Usage: $0 {install|start|stop|restart|status}" >&2
    exit 2
    ;;
esac
