#!/usr/bin/env bash
set -euo pipefail

cache="${1:-${HOME}/.cache/pi-gui-agent/androidworld}"
mkdir -p "${cache}"

download() {
  local name="$1"
  local url="$2"
  local destination="${cache}/${name}"
  if [[ -s "${destination}" ]]; then
    printf 'cached %s\n' "${name}"
    return
  fi
  printf 'download %s\n' "${name}"
  curl --silent --show-error --fail --location --retry 3 --connect-timeout 20 \
    --output "${destination}.partial" "${url}"
  mv "${destination}.partial" "${destination}"
}

while read -r name url; do
  download "${name}" "${url}" &
  while (( $(jobs -rp | wc -l) >= ${PI_GUI_DOWNLOAD_JOBS:-8} )); do
    wait -n
  done
done <<'RESOURCES'
ADBKeyboard.apk https://github.com/senzhk/ADBKeyBoard/raw/master/ADBKeyboard.apk
accessibility_forwarder.apk https://storage.googleapis.com/android_env-tasks/2024.05.13-accessibility_forwarder.apk
androidworld.apk https://storage.googleapis.com/gresearch/android_world/androidworld.apk
clipper.apk https://storage.googleapis.com/gresearch/android_world/clipper.apk
code.name.monkey.retromusic_10603.apk https://storage.googleapis.com/gresearch/android_world/code.name.monkey.retromusic_10603.apk
com.arduia.expense_11.apk https://storage.googleapis.com/gresearch/android_world/com.arduia.expense_11.apk
com.dimowner.audiorecorder_926.apk https://storage.googleapis.com/gresearch/android_world/com.dimowner.audiorecorder_926.apk
com.flauschcode.broccoli_1020600.apk https://storage.googleapis.com/gresearch/android_world/com.flauschcode.broccoli_1020600.apk
com.simplemobiletools.calendar.pro_238.apk https://storage.googleapis.com/gresearch/android_world/com.simplemobiletools.calendar.pro_238.apk
com.simplemobiletools.draw.pro_79.apk https://storage.googleapis.com/gresearch/android_world/com.simplemobiletools.draw.pro_79.apk
com.simplemobiletools.gallery.pro_396.apk https://storage.googleapis.com/gresearch/android_world/com.simplemobiletools.gallery.pro_396.apk
com.simplemobiletools.smsmessenger_85.apk https://storage.googleapis.com/gresearch/android_world/com.simplemobiletools.smsmessenger_85.apk
de.dennisguse.opentracks_5705.apk https://storage.googleapis.com/gresearch/android_world/de.dennisguse.opentracks_5705.apk
Liechtenstein_europe.obf https://storage.googleapis.com/gresearch/android_world/Liechtenstein_europe.obf
miniwobapp.apk https://storage.googleapis.com/gresearch/android_world/miniwobapp.apk
net.cozic.joplin_2097740.apk https://storage.googleapis.com/gresearch/android_world/net.cozic.joplin_2097740.apk
net.gsantner.markor_146.apk https://storage.googleapis.com/gresearch/android_world/net.gsantner.markor_146.apk
net.osmand-4.6.13.apk https://storage.googleapis.com/gresearch/android_world/net.osmand-4.6.13.apk
org.tasks_130605.apk https://storage.googleapis.com/gresearch/android_world/org.tasks_130605.apk
org.videolan.vlc_13050407.apk https://storage.googleapis.com/gresearch/android_world/org.videolan.vlc_13050407.apk
org.videolan.vlc_13050408.apk https://storage.googleapis.com/gresearch/android_world/org.videolan.vlc_13050408.apk
RESOURCES
wait
printf 'AndroidWorld cache ready: %s\n' "${cache}"
