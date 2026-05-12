#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VOICIUM_VERSION:-0.1.0}"
ARCHITECTURE="${VOICIUM_DEB_ARCH:-all}"
PACKAGE="voicium"
BUILD_DIR="${ROOT_DIR}/build/deb"
PACKAGE_ROOT="${BUILD_DIR}/${PACKAGE}_${VERSION}_${ARCHITECTURE}"
DIST_DIR="${ROOT_DIR}/dist/deb"
VENV_DIR="${PACKAGE_ROOT}/opt/voicium"

rm -rf "${PACKAGE_ROOT}"
mkdir -p \
  "${PACKAGE_ROOT}/DEBIAN" \
  "${PACKAGE_ROOT}/usr/bin" \
  "${PACKAGE_ROOT}/usr/lib/systemd/user" \
  "${PACKAGE_ROOT}/usr/share/doc/voicium" \
  "${VENV_DIR}"

uv export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt > "${BUILD_DIR}/requirements.txt"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install -r "${BUILD_DIR}/requirements.txt"
"${VENV_DIR}/bin/pip" install "${ROOT_DIR}"

cat > "${PACKAGE_ROOT}/usr/bin/voicium" <<'EOF'
#!/usr/bin/env sh
exec /opt/voicium/bin/voicium "$@"
EOF
chmod 0755 "${PACKAGE_ROOT}/usr/bin/voicium"

install -m 0644 "${ROOT_DIR}/resources/systemd/voicium.service" \
  "${PACKAGE_ROOT}/usr/lib/systemd/user/voicium.service"
install -m 0644 "${ROOT_DIR}/README.md" "${PACKAGE_ROOT}/usr/share/doc/voicium/README.md"
install -m 0644 "${ROOT_DIR}/docs/ubuntu-advanced-architecture.md" \
  "${PACKAGE_ROOT}/usr/share/doc/voicium/ubuntu-advanced-architecture.md"
install -m 0644 "${ROOT_DIR}/docs/ubuntu-mvp-plan.md" \
  "${PACKAGE_ROOT}/usr/share/doc/voicium/ubuntu-mvp-plan.md"
install -m 0644 "${ROOT_DIR}/docs/ubuntu-release-candidate.md" \
  "${PACKAGE_ROOT}/usr/share/doc/voicium/ubuntu-release-candidate.md"

cat > "${PACKAGE_ROOT}/DEBIAN/control" <<EOF
Package: voicium
Version: ${VERSION}
Section: sound
Priority: optional
Architecture: ${ARCHITECTURE}
Maintainer: Ruslan <ruslan@example.invalid>
Depends: python3 (>= 3.12), python3-venv, ffmpeg, pulseaudio-utils, wl-clipboard | xclip | xsel, libnotify-bin
Recommends: ydotool, xdotool, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, python3-gi
Description: Fast Russian push-to-talk dictation for Ubuntu
 Voicium is a local-first Ubuntu dictation daemon for Russian push-to-talk
 voice input with local Whisper transcription and clipboard fallback.
EOF

find "${PACKAGE_ROOT}" -type d -exec chmod 0755 {} +
mkdir -p "${DIST_DIR}"
dpkg-deb --build --root-owner-group "${PACKAGE_ROOT}" \
  "${DIST_DIR}/${PACKAGE}_${VERSION}_${ARCHITECTURE}.deb"
