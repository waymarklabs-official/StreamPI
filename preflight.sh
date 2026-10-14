#!/usr/bin/env bash
# StreamPI preflight — verifies every assumption the installer depends on.
# Read-only: changes nothing. Run this first.
#
#   sudo ./preflight.sh
#
# Exit 0 = safe to install. Exit 1 = a hard assumption failed.

set -uo pipefail

PASS=0; WARN=0; FAIL=0
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; PASS=$((PASS+1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; WARN=$((WARN+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
head() { printf '\n\033[1m%s\033[0m\n' "$*"; }

head "Hardware"

MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
case "$MODEL" in
  *"Raspberry Pi 5"*) ok "$MODEL"
      echo "        note: Pi 5 has no H.264 hardware decode block; it decodes 1080p"
      echo "        H.264 in software comfortably. Cache stays capped at 1080p." ;;
  *"Raspberry Pi 4"*) ok "$MODEL (H.264 hardware decode available)" ;;
  *"Raspberry Pi 3"*) warn "$MODEL — 1080p H.264 is fine, but Chromium + Widevine
        for Netflix/Prime/Paramount will be painful to unusable on a Pi 3." ;;
  unknown) bad "Could not read /proc/device-tree/model — is this a Raspberry Pi?" ;;
  *) warn "$MODEL — untested model" ;;
esac

ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
if [ "$ARCH" = "arm64" ]; then
  ok "64-bit userland ($ARCH)"
else
  bad "Userland is $ARCH, not arm64. Chromium's Widevine CDM on Raspberry Pi OS
        is built for 64-bit. A 32-bit install cannot play any DRM service."
fi

head "Operating system"

if [ -r /etc/os-release ]; then
  . /etc/os-release
  ok "${PRETTY_NAME:-unknown}"
  case "${VERSION_CODENAME:-}" in
    bookworm|trixie) ok "Codename ${VERSION_CODENAME} — supported" ;;
    bullseye) bad "Bullseye ships the legacy fkms stack and no NetworkManager by
        default. This kit targets Bookworm or newer. Re-image before installing." ;;
    *) warn "Codename '${VERSION_CODENAME:-none}' is untested" ;;
  esac
else
  bad "No /etc/os-release"
fi

if [ -e /etc/libreelec-release ] || [ -d /storage/.kodi ]; then
  bad "This looks like LibreELEC or OSMC, not Raspberry Pi OS. The root filesystem
        is read-only and there is no apt or Chromium, so this kit will not apply.
        (Raspberry Pi Connect only runs on Raspberry Pi OS, which is why the kit
        assumes it.)"
fi

head "Graphics / HDMI audio"

BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if [ -f "$BOOTCFG" ]; then
  ok "Boot config at $BOOTCFG"
  if grep -qE '^\s*dtoverlay=vc4-kms-v3d' "$BOOTCFG"; then
    ok "Full KMS driver (vc4-kms-v3d) enabled — required for vc4-hdmi ALSA cards"
  elif grep -qE '^\s*dtoverlay=vc4-fkms-v3d' "$BOOTCFG"; then
    bad "Legacy fkms driver is enabled. The vc4-hdmi-0 ALSA card only exists under
        full KMS. The installer will offer to switch this (needs a reboot)."
  else
    warn "No vc4 overlay found. Bookworm defaults to full KMS, so this is usually
        fine, but the installer will set it explicitly."
  fi
else
  bad "No config.txt found at /boot/firmware or /boot"
fi

if command -v aplay >/dev/null 2>&1; then
  if aplay -l 2>/dev/null | grep -q 'vc4hdmi0'; then
    CARDNUM="$(aplay -l 2>/dev/null | sed -n 's/^card \([0-9]*\): vc4hdmi0 .*/\1/p' | head -1)"
    ok "ALSA card vc4hdmi0 present (card $CARDNUM) — this is your vc4-HDMI-0 output"
  elif aplay -l 2>/dev/null | grep -q 'vc4hdmi'; then
    warn "Found a vc4hdmi card but not vc4hdmi0 specifically:"
    aplay -l 2>/dev/null | grep vc4hdmi | sed 's/^/        /'
  else
    bad "No vc4hdmi ALSA card. Either the TV was off/disconnected at boot, or full
        KMS is not active. Connect HDMI port 0 (the one nearest USB-C), power the
        TV on, reboot, and re-run. The installer also pins a video mode so the
        card exists even when the TV is off."
  fi
else
  bad "aplay not found — install alsa-utils"
fi

head "Network"

if command -v nmcli >/dev/null 2>&1; then
  ok "NetworkManager present ($(nmcli --version | awk '{print $NF}'))"
  if nmcli -t -f DEVICE,TYPE device 2>/dev/null | grep -q ':wifi$'; then
    WIFIDEV="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}')"
    ok "WiFi device: $WIFIDEV"
    # AP mode is mandatory for the captive portal.
    if iw list 2>/dev/null | grep -A10 'Supported interface modes' | grep -q 'AP$\|\* AP'; then
      ok "WiFi adapter advertises AP mode — captive portal can raise a hotspot"
    else
      warn "Could not confirm AP mode support via 'iw list'. The built-in Pi
        adapter does support it; a USB dongle might not."
    fi
  else
    bad "No WiFi device found. The captive portal has nothing to broadcast on."
  fi
else
  bad "nmcli not found. This kit drives WiFi entirely through NetworkManager."
fi

head "Disk space"

ROOTDEV="$(df -P / | awk 'NR==2{print $1}')"
TOTAL_K="$(df -P / | awk 'NR==2{print $2}')"
AVAIL_K="$(df -P / | awk 'NR==2{print $4}')"
TOTAL_G=$((TOTAL_K/1024/1024)); AVAIL_G=$((AVAIL_K/1024/1024))
ok "Root ($ROOTDEV): ${TOTAL_G}G total, ${AVAIL_G}G free"

# Chromium + cage + yt-dlp + ffmpeg land around 1.2G; reserve 3G of headroom on
# top of that so the box never wedges itself on a full disk.
NEED_G=5
if [ "$AVAIL_G" -lt "$NEED_G" ]; then
  bad "Only ${AVAIL_G}G free. Need ~${NEED_G}G for packages plus safety headroom
        before any video cache exists."
else
  BUDGET=$((AVAIL_G - NEED_G))
  ok "Projected YouTube cache budget after install: ~${BUDGET}G"
  if [ "$BUDGET" -lt 8 ]; then
    warn "That is a small cache. Expect it to hold only a handful of videos."
  fi
fi

head "Existing Kodi"

if command -v kodi >/dev/null 2>&1 || command -v kodi-standalone >/dev/null 2>&1; then
  ok "Kodi installed ($(kodi --version 2>/dev/null | head -1 || echo 'version unknown'))"
  if kodi --help 2>&1 | grep -q 'gbm'; then
    ok "Kodi supports --windowing=gbm (runs fullscreen with no desktop)"
  else
    warn "Could not confirm GBM windowing support. The installer falls back to
        launching Kodi inside the existing desktop session if GBM is missing."
  fi
else
  warn "Kodi not installed yet — the installer will apt-get it."
fi

for u in kodi streampi-portal streampi-netgate; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^${u}\.service"; then
    warn "A unit named ${u}.service already exists. The installer backs up any
        file it replaces to <file>.streampi-backup."
  fi
done

head "Summary"
printf '  %d passed, %d warnings, %d failures\n\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Resolve the FAIL items above before running install.sh."
  exit 1
fi
echo "Safe to run: sudo ./install.sh"
exit 0
