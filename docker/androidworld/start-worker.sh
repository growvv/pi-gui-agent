#!/usr/bin/env bash
set -euo pipefail

console_port="${ANDROIDWORLD_CONSOLE_PORT:-5554}"
grpc_port="${ANDROIDWORLD_GRPC_PORT:-8554}"
serial="emulator-${console_port}"
log_file="${ANDROIDWORLD_EMULATOR_LOG:-/output/emulator.log}"

avd_dir="/root/.android/avd"
if [[ ! -f "${avd_dir}/${EMULATOR_NAME}.avd/config.ini" ]]; then
  mkdir -p "${avd_dir}"
  cp -a /opt/android-avd-template/. "${avd_dir}/"
fi

# Containers cannot still own these locks after exit. Remove leftovers from a
# force-killed worker so the next launch can inspect or replace its snapshot.
find "${avd_dir}" -type f -name '*.lock' -delete
rm -f "${avd_dir}/${EMULATOR_NAME}.avd/read-snapshot.txt"

emulator "@${EMULATOR_NAME}" -no-window -no-boot-anim \
  -no-snapshot-save \
  -memory 2048 -accel on -port "${console_port}" -grpc "${grpc_port}" -gpu off \
  >"${log_file}" 2>&1 &
emulator_pid=$!

cleanup() {
  adb -s "${serial}" emu kill >/dev/null 2>&1 || true
  kill "${emulator_pid}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

deadline=$((SECONDS + ${ANDROIDWORLD_STARTUP_TIMEOUT_SECONDS:-360}))
until [[ "$(adb -s "${serial}" shell getprop sys.boot_completed 2>/dev/null || true)" == 1 ]]; do
  if ! kill -0 "${emulator_pid}" 2>/dev/null; then
    tail -120 "${log_file}"
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    echo "Emulator ${serial} did not boot before the startup timeout." >&2
    tail -120 "${log_file}"
    exit 1
  fi
  sleep 5
done

# APKs are downloaded once on the host and exposed through a read-only mount.
if [[ -f /download-cache/ADBKeyboard.apk ]] && \
   ! adb -s "${serial}" shell pm path com.android.adbkeyboard >/dev/null 2>&1; then
  adb -s "${serial}" install -r /download-cache/ADBKeyboard.apk \
    > /output/adb-keyboard-install.log 2>&1
fi

set +e
"$@"
status=$?
set -e
# AndroidWorld runs as root, while output belongs to the host developer UID.
chown -R "${ANDROIDWORLD_AGENT_USER:-agent}" /output
if [[ -n "${ANDROIDWORLD_HOST_UID:-}" && -n "${ANDROIDWORLD_HOST_GID:-}" ]]; then
  chown -R "${ANDROIDWORLD_HOST_UID}:${ANDROIDWORLD_HOST_GID}" /root/.android/avd
fi
exit "${status}"
