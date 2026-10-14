#!/usr/bin/env bash
# StreamPI installer — turns a Raspberry Pi OS box running Kodi into a
# Roku-style streaming appliance. Safe to re-run; every step is idempotent and
# anything it replaces is kept as <file>.streampi-backup.
#
#   sudo ./install.sh                 full install
#   sudo ./install.sh --user lights   install for a specific user
#   sudo ./install.sh --no-apt        skip package installation
#   sudo ./install.sh --update-ytdlp  refresh yt-dlp only
#   sudo ./install.sh --uninstall     remove StreamPI (keeps cached videos)

set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/usr/local/bin
ETC=/etc/streampi
LIB=/var/lib/streampi
DO_APT=1
ACTION=install
TARGET_USER=""

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    \033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --user) TARGET_USER="${2:-}"; shift 2 ;;
    --no-apt) DO_APT=0; shift ;;
    --update-ytdlp) ACTION=ytdlp; shift ;;
    --uninstall) ACTION=uninstall; shift ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "run with sudo"

# ---------------------------------------------------------------- user ------
if [ -z "$TARGET_USER" ]; then
  TARGET_USER="${SUDO_USER:-}"
fi
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = root ]; then
  # Fall back to the first ordinary login user.
  TARGET_USER="$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)"
fi
id "$TARGET_USER" >/dev/null 2>&1 || die "no such user: $TARGET_USER"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
KODI_USERDATA="$TARGET_HOME/.kodi/userdata"
KODI_ADDONS="$TARGET_HOME/.kodi/addons"

backup() { [ -e "$1" ] && [ ! -e "$1.streampi-backup" ] && cp -a "$1" "$1.streampi-backup"; return 0; }

install_ytdlp() {
  say "Installing yt-dlp"
  # Debian's packaged yt-dlp lags YouTube's extractor changes by months and
  # breaks silently. The upstream binary self-updates and is the only version
  # worth running against a moving target.
  local url=https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux_aarch64
  if curl -fsSL --retry 3 -o "$PREFIX/yt-dlp.new" "$url"; then
    chmod 0755 "$PREFIX/yt-dlp.new"
    if "$PREFIX/yt-dlp.new" --version >/dev/null 2>&1; then
      mv "$PREFIX/yt-dlp.new" "$PREFIX/yt-dlp"
      info "yt-dlp $("$PREFIX/yt-dlp" --version)"
    else
      rm -f "$PREFIX/yt-dlp.new"
      warn "downloaded yt-dlp did not run; falling back to apt"
      apt-get install -y yt-dlp >/dev/null 2>&1 || warn "apt yt-dlp failed too"
    fi
  else
    warn "could not download yt-dlp; trying apt"
    apt-get install -y yt-dlp >/dev/null 2>&1 || warn "apt yt-dlp failed too"
  fi
}

# ------------------------------------------------------------ uninstall -----
if [ "$ACTION" = uninstall ]; then
  say "Removing StreamPI"
  systemctl disable --now streampi-ytcache.timer streampi-netgate.service 2>/dev/null
  systemctl stop 'streampi-app@*.service' streampi-portal.service 2>/dev/null
  rm -f /etc/systemd/system/{streampi-netgate,streampi-portal,streampi-ytcache}.service \
        /etc/systemd/system/streampi-ytcache.timer \
        /etc/systemd/system/streampi-app@.service
  rm -f /etc/polkit-1/rules.d/50-streampi.rules
  rm -f /etc/NetworkManager/dnsmasq-shared.d/streampi-portal.conf
  rm -f "$PREFIX"/streampi-{netgate,portal,app,ytcache,audio-setup}
  for f in /etc/asound.conf /etc/systemd/system/kodi.service; do
    if [ -e "$f.streampi-backup" ]; then mv "$f.streampi-backup" "$f"; info "restored $f"
    else rm -f "$f"; fi
  done
  rm -rf "$KODI_ADDONS"/{service.streampi.ytcache,plugin.video.streampi.apps}
  systemctl daemon-reload
  info "Cached videos left in $LIB — delete manually if you want the space back."
  exit 0
fi

if [ "$ACTION" = ytdlp ]; then install_ytdlp; exit 0; fi

# ----------------------------------------------------------- sanity ---------
say "Checking the machine"
. /etc/os-release
info "user:  $TARGET_USER (uid $TARGET_UID, home $TARGET_HOME)"
info "os:    ${PRETTY_NAME:-unknown} ($(dpkg --print-architecture))"
info "model: $(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
[ "$(dpkg --print-architecture)" = arm64 ] || \
  die "32-bit userland: Widevine (and therefore every DRM service) will not work"
[ -d /etc/NetworkManager ] || die "NetworkManager not found; this kit drives WiFi through nmcli"

AVAIL_G=$(df -P / | awk 'NR==2{print int($4/1024/1024)}')
info "disk:  ${AVAIL_G}G free"
[ "$AVAIL_G" -ge 5 ] || die "need at least 5G free before installing"

# ----------------------------------------------------------- packages -------
if [ "$DO_APT" -eq 1 ]; then
  say "Installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || warn "apt update had problems; continuing"

  # cage      single-window Wayland compositor -> fullscreen Chromium, no desktop
  # qrencode  draws the WiFi join QR on the TV
  # alsa-utils provides aplay, used to find the vc4-hdmi card
  PKGS=(cage qrencode alsa-utils python3 curl ffmpeg policykit-1 network-manager)
  command -v kodi >/dev/null 2>&1 || PKGS+=(kodi kodi-bin)

  # Chromium is named differently across Raspberry Pi OS releases.
  if apt-cache show chromium-browser >/dev/null 2>&1; then PKGS+=(chromium-browser)
  elif apt-cache show chromium >/dev/null 2>&1; then PKGS+=(chromium)
  else warn "no chromium package found; the streaming apps will not work"; fi

  # The Widevine CDM is a separate package on recent images.
  apt-cache show libwidevinecdm0 >/dev/null 2>&1 && PKGS+=(libwidevinecdm0)

  info "apt-get install ${PKGS[*]}"
  apt-get install -y "${PKGS[@]}" || warn "some packages failed; see output above"
  install_ytdlp
fi

# -------------------------------------------------------------- dirs --------
say "Creating directories"
install -d -m 0755 "$ETC"
install -d -m 0755 -o "$TARGET_USER" -g "$TARGET_USER" "$LIB" "$LIB/youtube" "$LIB/chromium"
info "$ETC, $LIB/youtube, $LIB/chromium"

# ------------------------------------------------------------- binaries -----
say "Installing programs"
for f in streampi-netgate streampi-app streampi-ytcache streampi-audio-setup; do
  install -m 0755 "$SRC/bin/$f" "$PREFIX/$f"
  info "$PREFIX/$f"
done
install -m 0755 "$SRC/portal/streampi-portal" "$PREFIX/streampi-portal"
info "$PREFIX/streampi-portal"

# --------------------------------------------------------------- conf -------
say "Installing configuration"
if [ ! -e "$ETC/ytcache.conf" ]; then
  install -m 0644 "$SRC/config/ytcache.conf" "$ETC/ytcache.conf"
  # Size the cache from the disk we actually have rather than the template's
  # guess: free space, minus the reserve, minus room for packages and growth.
  BUDGET=$(( AVAIL_G - 4 - 2 ))
  [ "$BUDGET" -lt 2 ] && BUDGET=2
  sed -i "s/^max_cache_gb = .*/max_cache_gb = ${BUDGET}/" "$ETC/ytcache.conf"
  info "ytcache.conf (cache ceiling set to ${BUDGET}G from ${AVAIL_G}G free)"
else
  info "ytcache.conf exists; left alone"
fi
[ -e "$ETC/apps.conf" ] || install -m 0644 "$SRC/config/apps.conf" "$ETC/apps.conf"

# A placeholder so the permissions are right before any cookies are added.
if [ ! -e "$ETC/youtube-cookies.txt" ]; then
  install -m 0600 -o "$TARGET_USER" /dev/null "$ETC/youtube-cookies.txt"
  info "youtube-cookies.txt placeholder (0600) — see README to populate"
fi

# -------------------------------------------------------------- audio -------
say "Configuring ALSA for vc4-HDMI-0"
backup /etc/asound.conf
install -m 0644 "$SRC/config/asound.conf" /etc/asound.conf
info "/etc/asound.conf -> card vc4hdmi0"

BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if [ -f "$BOOTCFG" ]; then
  backup "$BOOTCFG"
  if ! grep -qE '^\s*dtoverlay=vc4-kms-v3d' "$BOOTCFG"; then
    if grep -qE '^\s*dtoverlay=vc4-fkms-v3d' "$BOOTCFG"; then
      sed -i 's/^\s*dtoverlay=vc4-fkms-v3d/dtoverlay=vc4-kms-v3d/' "$BOOTCFG"
      warn "switched fkms -> full KMS; a reboot is required for HDMI audio"
    else
      printf '\n# StreamPI: full KMS is required for the vc4-hdmi ALSA cards\ndtoverlay=vc4-kms-v3d\n' >> "$BOOTCFG"
      warn "enabled full KMS; a reboot is required for HDMI audio"
    fi
  else
    info "full KMS already enabled"
  fi
fi

# Pin a mode so the HDMI pipeline (and its ALSA card) exists even when the box
# boots with the TV off -- otherwise Kodi starts with no audio device at all and
# has to be restarted once the TV wakes up.
CMDLINE=/boot/firmware/cmdline.txt
[ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
if [ -f "$CMDLINE" ] && ! grep -q 'video=HDMI-A-1' "$CMDLINE"; then
  backup "$CMDLINE"
  sed -i '1s/$/ video=HDMI-A-1:1920x1080@60D/' "$CMDLINE"
  info "pinned HDMI-A-1 to 1080p60 so audio exists with the TV off"
fi

# ---------------------------------------------------------------- kodi ------
say "Configuring Kodi"
install -d -o "$TARGET_USER" -g "$TARGET_USER" "$KODI_USERDATA" "$KODI_ADDONS"

backup "$KODI_USERDATA/advancedsettings.xml"
install -m 0644 -o "$TARGET_USER" -g "$TARGET_USER" \
  "$SRC/config/advancedsettings.xml" "$KODI_USERDATA/advancedsettings.xml"
info "advancedsettings.xml (cache tuning, 75% watched threshold)"

if [ -e "$KODI_USERDATA/sources.xml" ]; then
  if grep -q "$LIB/youtube" "$KODI_USERDATA/sources.xml"; then
    info "sources.xml already has the YouTube Cache source"
  else
    backup "$KODI_USERDATA/sources.xml"
    # Insert our source rather than clobbering any the user already added.
    python3 - "$KODI_USERDATA/sources.xml" "$LIB/youtube/" <<'PY'
import sys, xml.etree.ElementTree as ET
path, cache = sys.argv[1], sys.argv[2]
tree = ET.parse(path); root = tree.getroot()
video = root.find("video") or ET.SubElement(root, "video")
src = ET.SubElement(video, "source")
ET.SubElement(src, "name").text = "YouTube Cache"
p = ET.SubElement(src, "path"); p.set("pathversion", "1"); p.text = cache
ET.SubElement(src, "allowsharing").text = "false"
ET.indent(tree, space="    ")
tree.write(path, encoding="UTF-8", xml_declaration=True)
PY
    chown "$TARGET_USER:$TARGET_USER" "$KODI_USERDATA/sources.xml"
    info "merged YouTube Cache into existing sources.xml"
  fi
else
  install -m 0644 -o "$TARGET_USER" -g "$TARGET_USER" \
    "$SRC/config/sources.xml" "$KODI_USERDATA/sources.xml"
  info "sources.xml with the YouTube Cache source"
fi

for a in service.streampi.ytcache plugin.video.streampi.apps; do
  rm -rf "${KODI_ADDONS:?}/$a"
  cp -a "$SRC/addons/$a" "$KODI_ADDONS/$a"
  chown -R "$TARGET_USER:$TARGET_USER" "$KODI_ADDONS/$a"
  info "addon $a"
done

# ------------------------------------------------------------- systemd ------
say "Installing services"
for u in streampi-netgate.service streampi-portal.service \
         streampi-ytcache.service streampi-ytcache.timer \
         streampi-app@.service kodi.service; do
  tgt=/etc/systemd/system/$u
  [ "$u" = kodi.service ] && backup "$tgt"
  sed -e "s/@USER@/$TARGET_USER/g" -e "s/@UID@/$TARGET_UID/g" \
      "$SRC/systemd/$u" > "$tgt"
  chmod 0644 "$tgt"
  info "$u"
done

install -d -m 0755 /etc/polkit-1/rules.d
sed "s/@USER@/$TARGET_USER/g" "$SRC/config/50-streampi.rules" \
  > /etc/polkit-1/rules.d/50-streampi.rules
chmod 0644 /etc/polkit-1/rules.d/50-streampi.rules
info "polkit rule so $TARGET_USER can switch between Kodi and the apps"

# Kodi draws through DRM and reads the remote directly; without these groups it
# cannot open /dev/dri or /dev/input and dies at startup.
say "Adding $TARGET_USER to hardware groups"
for g in video render input audio tty dialout plugdev; do
  getent group "$g" >/dev/null && usermod -aG "$g" "$TARGET_USER"
done
info "$(id -nG "$TARGET_USER")"

systemctl daemon-reload
systemctl enable streampi-netgate.service >/dev/null 2>&1
systemctl enable streampi-ytcache.timer  >/dev/null 2>&1
systemctl enable kodi.service            >/dev/null 2>&1
# Kodi owns tty1; a getty fighting it for the console causes flickering.
systemctl disable --now getty@tty1.service >/dev/null 2>&1
info "enabled: kodi, streampi-netgate, streampi-ytcache.timer"

# --------------------------------------------------------------- audio ------
say "Pointing Kodi at vc4-HDMI-0"
systemctl stop kodi.service 2>/dev/null
if "$PREFIX/streampi-audio-setup" --user "$TARGET_USER"; then
  :
else
  warn "audio setup could not complete — most likely the TV is off, so the"
  warn "vc4hdmi0 card has not enumerated. Re-run after rebooting with the TV on:"
  warn "  sudo systemctl stop kodi && sudo streampi-audio-setup --user $TARGET_USER"
fi

# ------------------------------------------------------------- verify -------
say "Verifying the streaming apps"
"$PREFIX/streampi-app" --check || warn "see the failures above before using the apps"

# -------------------------------------------------------------- done --------
say "Done"
cat <<EOF
    Reboot to pick up the KMS/HDMI boot changes and the new group memberships:

        sudo reboot

    After it comes back up:
      * No known WiFi?  The TV shows a QR code; join "StreamPI-Setup" from your
        phone and pick your network.
      * Kodi starts fullscreen on vc4-HDMI-0 over ALSA.
      * Streaming services live under Add-ons > Streaming Apps.
      * The YouTube cache starts filling 10 minutes after boot, then every 4h.

    Useful commands:
        streampi-ytcache status       what is cached, how much room is left
        streampi-app --check          chromium / widevine / cage health
        journalctl -u kodi -b         why Kodi did not start
        journalctl -u streampi-portal -b   WiFi setup troubles

    To get YOUR recommendations rather than a generic feed, install a cookie
    file at $ETC/youtube-cookies.txt — see README.md, "Personalised
    recommendations", including the account-risk tradeoff.
EOF
