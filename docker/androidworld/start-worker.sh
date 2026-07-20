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

# Emulator 36.x runs the modem simulator over IPv6 loopback. QEMU resolves
# that endpoint with AI_ADDRCONFIG, which requires a non-loopback IPv6 address
# on the worker container. Fail early instead of turning all SMS tasks into
# ordinary benchmark failures when the Docker network is misconfigured.
telephony_deadline=$((SECONDS + ${ANDROIDWORLD_TELEPHONY_TIMEOUT_SECONDS:-60}))
while true; do
  sim_state="$(adb -s "${serial}" shell getprop gsm.sim.state 2>/dev/null || true)"
  service_state="$(
    adb -s "${serial}" shell dumpsys telephony.registry 2>/dev/null \
      | grep -m1 'mServiceState=' || true
  )"
  if [[ "${sim_state}" =~ (LOADED|READY) ]] && \
     [[ "${service_state}" == *'IN_SERVICE'* ]]; then
    break
  fi
  if grep -q 'Unable to connect character device modem' "${log_file}"; then
    echo 'Android emulator modem failed to start.' >&2
    echo 'Run this worker on an IPv6-enabled Docker network.' >&2
    grep 'Unable to connect character device modem' "${log_file}" >&2 || true
    exit 1
  fi
  if (( SECONDS >= telephony_deadline )); then
    echo "Android telephony did not become ready (SIM=${sim_state:-empty})." >&2
    echo "${service_state:-No telephony service state available.}" >&2
    exit 1
  fi
  sleep 2
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
