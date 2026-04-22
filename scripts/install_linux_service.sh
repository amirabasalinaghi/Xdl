#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

run_as_service_user() {
  local user="$1"
  shift
  if [[ "${EUID}" -eq 0 ]]; then
    su -s /bin/bash - "${user}" -c "$(printf '%q ' "$@")"
  else
    sudo -u "${user}" "$@"
  fi
}

SERVICE_NAME="xdl-relay"
INSTALL_DIR="/opt/xdl-relay"
ENV_DIR="/etc/xdl-relay"
ENV_FILE="${ENV_DIR}/xdl-relay.env"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DEFAULT_USER="${SUDO_USER:-${USER}}"
DEFAULT_GROUP="$(id -gn "${DEFAULT_USER}")"

ask_default() {
  local key="$1"
  local prompt="$2"
  local default="$3"
  local value=""
  local env_value="${!key:-}"
  if [[ -n "${env_value}" ]]; then
    printf '%s' "${env_value}"
    return
  fi
  if [[ -r /dev/tty ]]; then
    read -r -p "${prompt} [${default}]: " value < /dev/tty
  else
    read -r -p "${prompt} [${default}]: " value
  fi
  if [[ -z "${value}" ]]; then
    value="${default}"
  fi
  printf '%s' "${value}"
}

ask_yes_no_default() {
  local key="$1"
  local prompt="$2"
  local default="$3"
  local value=""
  local env_value="${!key:-}"
  if [[ -n "${env_value}" ]]; then
    value="${env_value}"
  else
    if [[ -r /dev/tty ]]; then
      read -r -p "${prompt} [${default}]: " value < /dev/tty
    else
      read -r -p "${prompt} [${default}]: " value
    fi
  fi
  value="${value:-${default}}"
  value="${value,,}"
  [[ "${value}" == "y" || "${value}" == "yes" || "${value}" == "1" || "${value}" == "true" ]]
}

remove_previous_install() {
  echo
  echo "Removing previous ${SERVICE_NAME} installation..."
  ${SUDO} systemctl disable --now "${SERVICE_NAME}" >/dev/null 2>&1 || true
  ${SUDO} rm -f "${SERVICE_FILE}"
  ${SUDO} rm -rf "${INSTALL_DIR}"
  ${SUDO} rm -rf "${ENV_DIR}"
  ${SUDO} systemctl daemon-reload
}

prepare_reinstall() {
  echo
  echo "Previous installation detected. Reinstalling runtime while preserving data/settings..."
  ${SUDO} systemctl disable --now "${SERVICE_NAME}" >/dev/null 2>&1 || true
  ${SUDO} rm -f "${SERVICE_FILE}"
  ${SUDO} rm -rf "${INSTALL_DIR}/.venv"
  ${SUDO} systemctl daemon-reload
}

echo "== XDL Relay Linux Service Installer =="
echo "This installs only the relay runtime + Web UI."
echo "API keys, IDs, bot token, and relay behavior are configured in the Web UI after install."
echo

REPO_DIR="$(ask_default "REPO_DIR" "Path to this repository" "$(pwd)")"
SERVICE_USER="$(ask_default "SERVICE_USER" "Linux user to run the service" "${DEFAULT_USER}")"
SERVICE_GROUP="$(ask_default "SERVICE_GROUP" "Linux group to run the service" "${DEFAULT_GROUP}")"
POLL_INTERVAL_SECONDS="$(ask_default "POLL_INTERVAL_SECONDS" "POLL_INTERVAL_SECONDS" "30")"
DB_PATH="$(ask_default "DB_PATH" "DB_PATH (inside ${INSTALL_DIR})" "${INSTALL_DIR}/relay.db")"
MEDIA_DIR="$(ask_default "MEDIA_DIR" "MEDIA_DIR (inside ${INSTALL_DIR})" "${INSTALL_DIR}/media")"
WEBUI_HOST="$(ask_default "WEBUI_HOST" "WEBUI_HOST" "0.0.0.0")"
WEBUI_PORT="$(ask_default "WEBUI_PORT" "WEBUI_PORT" "8080")"

if [[ ! -f "${REPO_DIR}/pyproject.toml" ]]; then
  echo "Could not find pyproject.toml in ${REPO_DIR}."
  exit 1
fi

if ${SUDO} test -f "${SERVICE_FILE}" || ${SUDO} test -d "${INSTALL_DIR}" || ${SUDO} test -f "${ENV_FILE}"; then
  if ask_yes_no_default "REINSTALL" "Previous installation detected. Reinstall?" "yes"; then
    if ask_yes_no_default "RESET_INSTALL_STATE" "Remove existing database/media/environment settings too?" "no"; then
      remove_previous_install
    else
      prepare_reinstall
    fi
  else
    echo "Aborting install to avoid partial overwrite."
    exit 1
  fi
fi

echo
echo "Installing system dependencies (python3-venv if missing)..."
if command -v apt-get >/dev/null 2>&1; then
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y python3 python3-venv
elif command -v dnf >/dev/null 2>&1; then
  ${SUDO} dnf install -y python3
elif command -v yum >/dev/null 2>&1; then
  ${SUDO} yum install -y python3
else
  echo "No known package manager detected. Ensure Python 3.10+ and venv are installed."
fi

echo "Preparing directories..."
${SUDO} mkdir -p "${INSTALL_DIR}" "${ENV_DIR}" "${MEDIA_DIR}" "$(dirname "${DB_PATH}")"
${SUDO} chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"

VENV_PATH="${INSTALL_DIR}/.venv"

echo "Creating virtual environment at ${VENV_PATH}..."
run_as_service_user "${SERVICE_USER}" python3 -m venv "${VENV_PATH}"

echo "Installing application into virtual environment..."
run_as_service_user "${SERVICE_USER}" "${VENV_PATH}/bin/pip" install --upgrade pip
run_as_service_user "${SERVICE_USER}" "${VENV_PATH}/bin/pip" install "${REPO_DIR}"

echo "Writing environment file to ${ENV_FILE}..."
if ${SUDO} test -f "${ENV_FILE}"; then
  echo "Existing environment file detected; preserving current API/database settings."
else
  ${SUDO} tee "${ENV_FILE}" >/dev/null <<EOV
# Configure these in the Web UI after installation.
X_USER_ID=SET_IN_WEBUI
X_BEARER_TOKEN=SET_IN_WEBUI
TELEGRAM_BOT_TOKEN=SET_IN_WEBUI
TELEGRAM_CHAT_ID=SET_IN_WEBUI
POLL_INTERVAL_SECONDS=${POLL_INTERVAL_SECONDS}
DB_PATH=${DB_PATH}
MEDIA_DIR=${MEDIA_DIR}
RELAY_ENV_FILE=${ENV_FILE}
EOV
fi
${SUDO} chmod 600 "${ENV_FILE}"
${SUDO} chown "${SERVICE_USER}:${SERVICE_GROUP}" "${ENV_FILE}"

echo "Writing systemd service to ${SERVICE_FILE}..."
${SUDO} tee "${SERVICE_FILE}" >/dev/null <<EOS
[Unit]
Description=XDL Relay (X -> Telegram media relay)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_PATH}/bin/python -m xdl_relay --webui --host ${WEBUI_HOST} --port ${WEBUI_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOS

${SUDO} systemctl daemon-reload
${SUDO} systemctl enable --now "${SERVICE_NAME}"

echo
echo "Installation complete."
echo "Web UI URL: http://$(hostname -I | awk '{print $1}'):${WEBUI_PORT}"
echo "Local URL: http://${WEBUI_HOST}:${WEBUI_PORT}"
echo "Configure IDs, API keys, and bot settings in the Web UI."
echo "Check service status: sudo systemctl status ${SERVICE_NAME}"
echo "View logs: sudo journalctl -u ${SERVICE_NAME} -f"
