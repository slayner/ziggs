#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir="$(mktemp -d /tmp/ziggs-companion-linux.XXXXXX)"
output="$root/artifacts/linux"

if ! command -v xdg-open >/dev/null; then
  printf 'Instale xdg-utils antes de gerar o AppImage: sudo apt-get install -y xdg-utils\n' >&2
  exit 1
fi

cleanup() {
  local status=$?
  if [ "$status" -eq 0 ]; then
    rm -rf "$workdir"
  else
    printf 'Diretório de diagnóstico preservado: %s\n' "$workdir" >&2
  fi
}
trap cleanup EXIT

rsync -a --delete \
  --exclude node_modules \
  --exclude dist \
  --exclude src-tauri/target \
  --exclude artifacts \
  "$root/" "$workdir/"

(
  cd "$workdir"
  npm ci
  ./node_modules/.bin/tauri build --bundles deb,appimage --config src-tauri/tauri.linux.conf.json --verbose
)

mapfile -t artifacts < <(find "$workdir/src-tauri/target/release/bundle" -type f \( -name '*.deb' -o -name '*.AppImage' -o -name '*.sig' \))
if [ "${#artifacts[@]}" -eq 0 ]; then
  printf 'Nenhum bundle Linux foi gerado em %s\n' "$workdir/src-tauri/target/release/bundle" >&2
  exit 1
fi

mkdir -p "$output"
rm -f "$output"/*.deb "$output"/*.AppImage "$output"/*.sig
cp "${artifacts[@]}" "$output/"
ls -la "$output"
