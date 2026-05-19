#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VOICIUM_VERSION:-0.1.0}"
ARCHITECTURE="${VOICIUM_DEB_ARCH:-$(dpkg --print-architecture)}"
PACKAGE="voicium"
BUILD_DIR="${ROOT_DIR}/build/deb"
PACKAGE_ROOT="${BUILD_DIR}/${PACKAGE}_${VERSION}_${ARCHITECTURE}"
DIST_DIR="${ROOT_DIR}/dist/deb"
VENV_DIR="${PACKAGE_ROOT}/opt/voicium"
WHISPER_CPP_REF="${VOICIUM_WHISPER_CPP_REF:-v1.8.2}"
WHISPER_CPP_SRC="${BUILD_DIR}/whisper.cpp"
WHISPER_CPP_BUILD="${BUILD_DIR}/whisper.cpp-build"

rm -rf "${PACKAGE_ROOT}"
mkdir -p \
  "${PACKAGE_ROOT}/DEBIAN" \
  "${PACKAGE_ROOT}/usr/bin" \
  "${PACKAGE_ROOT}/usr/lib/voicium/bin" \
  "${PACKAGE_ROOT}/usr/lib/voicium/lib" \
  "${PACKAGE_ROOT}/usr/lib/systemd/user" \
  "${PACKAGE_ROOT}/usr/share/doc/voicium" \
  "${VENV_DIR}"

if [ ! -d "${WHISPER_CPP_SRC}/.git" ]; then
  rm -rf "${WHISPER_CPP_SRC}"
  git clone --depth 1 --branch "${WHISPER_CPP_REF}" \
    https://github.com/ggml-org/whisper.cpp.git "${WHISPER_CPP_SRC}"
fi
git -C "${WHISPER_CPP_SRC}" fetch --depth 1 origin "${WHISPER_CPP_REF}"
git -C "${WHISPER_CPP_SRC}" checkout --detach FETCH_HEAD
rm -rf "${WHISPER_CPP_BUILD}"
cmake -S "${WHISPER_CPP_SRC}" -B "${WHISPER_CPP_BUILD}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DWHISPER_BUILD_TESTS=OFF \
  -DWHISPER_BUILD_EXAMPLES=ON \
  -DWHISPER_BUILD_SERVER=OFF \
  -DGGML_NATIVE=OFF
cmake --build "${WHISPER_CPP_BUILD}" --config Release --target whisper-cli --parallel
install -m 0755 "${WHISPER_CPP_BUILD}/bin/whisper-cli" \
  "${PACKAGE_ROOT}/usr/lib/voicium/bin/whisper-cli"
install -m 0644 "${WHISPER_CPP_BUILD}"/src/libwhisper.so* \
  "${PACKAGE_ROOT}/usr/lib/voicium/lib/"
install -m 0644 "${WHISPER_CPP_BUILD}"/ggml/src/libggml*.so* \
  "${PACKAGE_ROOT}/usr/lib/voicium/lib/"

uv export --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt > "${BUILD_DIR}/requirements.txt"
python3 -m venv --system-site-packages "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install -r "${BUILD_DIR}/requirements.txt"
"${VENV_DIR}/bin/pip" install "${ROOT_DIR}"

cat > "${PACKAGE_ROOT}/usr/bin/voicium" <<'EOF'
#!/usr/bin/env sh
export PATH="/usr/lib/voicium/bin:${PATH}"
export LD_LIBRARY_PATH="/usr/lib/voicium/lib:${LD_LIBRARY_PATH:-}"
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
Depends: python3 (>= 3.12), python3-venv, ffmpeg, pulseaudio-utils, wl-clipboard | xclip | xsel, libnotify-bin, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, python3-gi, libgomp1, libstdc++6
Recommends: ydotool, xdotool
Description: Fast Russian push-to-talk dictation for Ubuntu
 Voicium is a local-first Ubuntu dictation daemon for Russian push-to-talk
 voice input with local Whisper transcription and clipboard fallback.
EOF

cat > "${PACKAGE_ROOT}/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env sh
set -eu

target_user="${SUDO_USER:-}"
if [ -z "${target_user}" ] || [ "${target_user}" = "root" ]; then
  exit 0
fi

target_home="$(getent passwd "${target_user}" | cut -d: -f6)"
config_path="${target_home}/.config/voicium/config.toml"
if [ ! -f "${config_path}" ]; then
  exit 0
fi

python3 - "${config_path}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

config_path = Path(sys.argv[1])
lines = config_path.read_text(encoding="utf-8").splitlines()
result: list[str] = []
in_transcription = False
seen_backend = False
seen_model_profile = False
seen_runtime_mode = False

for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        if in_transcription:
            if not seen_backend:
                result.append('backend = "auto"')
            if not seen_model_profile:
                result.append('model_profile = "small-q8_0"')
            if not seen_runtime_mode:
                result.append('runtime_mode = "small-q8_0"')
        in_transcription = stripped == "[transcription]"
        seen_backend = False
        seen_model_profile = False
        seen_runtime_mode = False
        result.append(line)
        continue

    if in_transcription and stripped.startswith("backend"):
        result.append('backend = "auto"')
        seen_backend = True
        continue
    if in_transcription and stripped.startswith("model_profile"):
        result.append('model_profile = "small-q8_0"')
        seen_model_profile = True
        continue
    if in_transcription and stripped.startswith("runtime_mode"):
        result.append('runtime_mode = "small-q8_0"')
        seen_runtime_mode = True
        continue

    result.append(line)

if in_transcription:
    if not seen_backend:
        result.append('backend = "auto"')
    if not seen_model_profile:
        result.append('model_profile = "small-q8_0"')
    if not seen_runtime_mode:
        result.append('runtime_mode = "small-q8_0"')

config_path.write_text("\n".join(result) + "\n", encoding="utf-8")
PY
EOF
chmod 0755 "${PACKAGE_ROOT}/DEBIAN/postinst"

find "${PACKAGE_ROOT}" -type d -exec chmod 0755 {} +
mkdir -p "${DIST_DIR}"
dpkg-deb --build --root-owner-group "${PACKAGE_ROOT}" \
  "${DIST_DIR}/${PACKAGE}_${VERSION}_${ARCHITECTURE}.deb"
