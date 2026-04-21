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

ask_required() {
  local key="$1"
  local prompt="${2:-$1}"
  local value=""
  local env_value="${!key:-}"
  if [[ -n "${env_value}" ]]; then
    printf '%s' "${env_value}"
    return
  fi
  while [[ -z "${value}" ]]; do
    if [[ -r /dev/tty ]]; then
      read -r -p "${prompt}: " value < /dev/tty
    else
      read -r -p "${prompt}: " value
    fi
    if [[ -z "${value}" ]]; then
      echo "This value is required."
    fi
  done
  printf '%s' "${value}"
}

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

echo "== XDL Relay Linux Service Installer =="
echo "This will install ${SERVICE_NAME} as a systemd service."
echo

REPO_DIR="$(ask_default "REPO_DIR" "Path to this repository" "$(pwd)")"
X_BEARER_TOKEN="$(ask_required "X_BEARER_TOKEN" "X_BEARER_TOKEN")"
X_USER_ID="$(ask_required "X_USER_ID" "X_USER_ID")"
TELEGRAM_BOT_TOKEN="$(ask_required "TELEGRAM_BOT_TOKEN" "TELEGRAM_BOT_TOKEN")"
TELEGRAM_CHAT_ID="$(ask_required "TELEGRAM_CHAT_ID" "TELEGRAM_CHAT_ID")"
POLL_INTERVAL_SECONDS="$(ask_default "POLL_INTERVAL_SECONDS" "POLL_INTERVAL_SECONDS" "30")"
SERVICE_USER="$(ask_default "SERVICE_USER" "Linux user to run the service" "${DEFAULT_USER}")"
SERVICE_GROUP="$(ask_default "SERVICE_GROUP" "Linux group to run the service" "${DEFAULT_GROUP}")"
DB_PATH="$(ask_default "DB_PATH" "DB_PATH (inside ${INSTALL_DIR})" "${INSTALL_DIR}/relay.db")"
MEDIA_DIR="$(ask_default "MEDIA_DIR" "MEDIA_DIR (inside ${INSTALL_DIR})" "${INSTALL_DIR}/media")"

if [[ ! -f "${REPO_DIR}/pyproject.toml" ]]; then
  echo "Could not find pyproject.toml in ${REPO_DIR}."
  exit 1
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
${SUDO} tee "${ENV_FILE}" >/dev/null <<EOV
X_BEARER_TOKEN=${X_BEARER_TOKEN}
X_USER_ID=${X_USER_ID}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
POLL_INTERVAL_SECONDS=${POLL_INTERVAL_SECONDS}
DB_PATH=${DB_PATH}
MEDIA_DIR=${MEDIA_DIR}
EOV
${SUDO} chmod 600 "${ENV_FILE}"
${SUDO} chown "root:root" "${ENV_FILE}"

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
ExecStart=${VENV_PATH}/bin/python -m xdl_relay
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOS

${SUDO} systemctl daemon-reload
${SUDO} systemctl enable --now "${SERVICE_NAME}"

echo
echo "Installation complete."
echo "Check service status: sudo systemctl status ${SERVICE_NAME}"
echo "View logs: sudo journalctl -u ${SERVICE_NAME} -f"
