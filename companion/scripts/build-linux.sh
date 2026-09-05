#!/usr/bin/env bash
# Gera localmente o .deb Linux assinado do Companion em um staging WSL nativo.
# A senha é digitada no TTY do WSL com entrada oculta e nunca chega ao PowerShell.
# Não publica artefatos, não altera manifestos e não acessa a VPS.
set -euo pipefail

source_path="${1:-}"
key_path="${2:-}"

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

if [[ -z "$source_path" || -z "$key_path" ]]; then
  fail "Uso: build-linux.sh <diretório-companion> <caminho-chave-privada>"
fi
if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
  fail "Execute pelo terminal Windows interativo para digitar a senha da chave com entrada oculta."
fi
exec </dev/tty >/dev/tty 2>&1

# A partir daqui, os prompts do assinador ficam vinculados ao terminal interativo
# mesmo quando o wrapper é executado por uma ferramenta que redireciona stdio.

# shellcheck disable=SC3045
if [[ ! -t 0 || ! -t 1 ]]; then
  fail "Não foi possível vincular a build ao terminal interativo."
fi
if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  fail "Esta build exige Linux x86_64."
fi
if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
  fail "Esta build exige uma distribuição Debian amd64."
fi
if [[ ! -d "$source_path" ]]; then
  fail "Diretório-fonte do Companion não está acessível no WSL."
fi
if [[ ! -r "$key_path" ]]; then
  fail "Chave privada de assinatura não está acessível no WSL."
fi

# Credenciais herdadas nunca podem alcançar a build. Depois da compilação, a
# senha será digitada no TTY e exposta apenas ao subprocesso do assinador.
inherited_signing_variables=()
for variable in TAURI_SIGNING_PRIVATE_KEY TAURI_SIGNING_PRIVATE_KEY_PATH TAURI_SIGNING_PRIVATE_KEY_PASSWORD TAURI_PRIVATE_KEY TAURI_PRIVATE_KEY_PATH TAURI_PRIVATE_KEY_PASSWORD TAURI_KEY_PASSWORD; do
  [[ -n "${!variable:-}" ]] && inherited_signing_variables+=("$variable")
done
if [[ "${#inherited_signing_variables[@]}" -gt 0 ]]; then
  fail "Remova as variáveis de assinatura herdadas antes de executar este script: ${inherited_signing_variables[*]}"
fi

# Remove qualquer herança de ferramentas/caches Windows e só permite executáveis
# Linux. A chave é passada ao Tauri depois por caminho, nunca por conteúdo.
unset NODE_PATH NPM_CONFIG_PREFIX npm_config_prefix NPM_CONFIG_CACHE npm_config_cache
unset RUSTFLAGS CC CXX AR LD CARGO_BUILD_TARGET
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export NPM_CONFIG_CACHE="$HOME/.cache/ziggs-companion/npm"
export CARGO_HOME="$HOME/.cache/ziggs-companion/cargo"
export RUSTUP_HOME="$HOME/.cache/ziggs-companion/rustup"
export XDG_CACHE_HOME="$HOME/.cache/ziggs-companion/xdg"

for tool in node npm cargo rustc pkg-config rsync dpkg-deb file sha256sum readelf ldd; do
  command -v "$tool" >/dev/null || fail "Dependência Linux ausente: $tool"
  resolved="$(readlink -f "$(command -v "$tool")")"
  case "$resolved" in
    /mnt/*|*.exe) fail "Ferramenta não Linux detectada: $tool" ;;
  esac
done

for package in gtk+-3.0 webkit2gtk-4.1 ayatana-appindicator3-0.1 librsvg-2.0; do
  pkg-config --exists "$package" || fail "Dependência Tauri ausente: $package"
done

home_path="$(realpath "$HOME")"
case "$home_path" in
  /mnt/*) fail "O HOME do WSL precisa estar em um filesystem Linux nativo." ;;
esac

cache_root="$HOME/.cache/ziggs-companion"
stage_root="$cache_root/builds"
mkdir -p "$stage_root" "$NPM_CONFIG_CACHE" "$CARGO_HOME" "$RUSTUP_HOME" "$XDG_CACHE_HOME"
stage="$(mktemp -d "$stage_root/companion-linux.XXXXXX")"

cleanup() {
  local status=$?
  unset signing_password TAURI_SIGNING_PRIVATE_KEY TAURI_SIGNING_PRIVATE_KEY_PATH TAURI_SIGNING_PRIVATE_KEY_PASSWORD TAURI_PRIVATE_KEY TAURI_PRIVATE_KEY_PATH TAURI_PRIVATE_KEY_PASSWORD TAURI_KEY_PASSWORD
  if [[ "$status" -eq 0 ]]; then
    rm -rf "$stage"
  else
    printf 'Staging de diagnóstico preservado: %s\n' "$stage" >&2
  fi
}
trap cleanup EXIT

stage_path="$(realpath "$stage")"
case "$stage_path" in
  /mnt/*) fail "O staging não pode ficar em /mnt." ;;
esac

# /mnt/c é somente a entrada da cópia. npm, Cargo, Tauri, caches e artefatos
# trabalham exclusivamente no staging ext4 dentro do HOME do WSL.
rsync -a \
  --exclude node_modules \
  --exclude dist \
  --exclude src-tauri/target \
  --exclude artifacts \
  "$source_path/" "$stage/"

cd "$stage"
export CARGO_TARGET_DIR="$stage/src-tauri/target"

case "$(pwd -P)" in
  /mnt/*) fail "O diretório de build não pode ficar em /mnt." ;;
esac

version="$(node -p "require('./src-tauri/tauri.conf.json').version")"
cargo_version="$(node -e "const fs=require('fs'); const m=fs.readFileSync('src-tauri/Cargo.toml','utf8').match(/^version\\s*=\\s*\"([^\"]+)\"/m); if (!m) process.exit(1); process.stdout.write(m[1])")"
npm_version="$(node -p "require('./package.json').version")"
[[ "$version" == "$cargo_version" ]] || fail "Versões Tauri e Cargo divergentes no staging."

linux_resources="$(node -e "const c=require('./src-tauri/tauri.linux.conf.json'); process.stdout.write(JSON.stringify(c.bundle?.resources ?? null))")"
[[ "$linux_resources" == "[]" ]] || fail "O overlay Linux precisa remover os recursos exclusivos de Windows."

printf '=== Build Linux do Ziggs Companion v%s ===\n' "$version"
printf 'npm metadata: v%s | Tauri/Cargo: v%s\n' "$npm_version" "$version"
printf '%s\n' 'Gerando o .deb sem chave; a senha será pedida somente após a compilação.'

npm ci --no-audit --no-fund
./node_modules/.bin/tauri build --no-sign --bundles deb --config src-tauri/tauri.linux.conf.json

bundle_dir="$CARGO_TARGET_DIR/release/bundle/deb"
mapfile -t debs < <(find "$bundle_dir" -maxdepth 1 -type f -name '*.deb' -print | sort)
if [[ "${#debs[@]}" -ne 1 ]]; then
  fail "Esperado exatamente um .deb; encontrados ${#debs[@]}."
fi
deb="${debs[0]}"

# O Tauri CLI não solicita senhas interativamente. A senha é lida sem eco no TTY
# somente agora e fica disponível apenas no ambiente do subprocesso do assinador.
# Ela não passa pelo PowerShell, pelos argumentos do WSL nem por arquivos ou logs.
printf 'Digite a senha da chave de assinatura: ' >/dev/tty
IFS= read -r -s signing_password </dev/tty
printf '\n' >/dev/tty
if ! TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$signing_password" \
  ./node_modules/.bin/tauri signer sign --private-key-path "$key_path" "$deb" </dev/tty; then
  unset signing_password
  fail "O Tauri não conseguiu assinar o pacote Debian."
fi
unset signing_password
signature="$deb.sig"
[[ -s "$signature" ]] || fail "O Tauri não produziu uma assinatura .sig válida."

package_name="$(dpkg-deb -f "$deb" Package)"
package_version="$(dpkg-deb -f "$deb" Version)"
package_architecture="$(dpkg-deb -f "$deb" Architecture)"
[[ "$package_name" == "ziggs-companion" ]] || fail "Pacote Debian inesperado: $package_name"
[[ "$package_version" == "$version" ]] || fail "Versão inesperada no .deb: $package_version"
[[ "$package_architecture" == "amd64" ]] || fail "Arquitetura inesperada no .deb: $package_architecture"

has_windows_resource=0
while IFS= read -r entry; do
  lower_entry="${entry,,}"
  case "$lower_entry" in
    *windivert*|*wintun*|*.dll|*.sys)
      printf 'Recurso Windows indevido no pacote: %s\n' "$entry" >&2
      has_windows_resource=1
      ;;
  esac
done < <(dpkg-deb --contents "$deb")
[[ "$has_windows_resource" -eq 0 ]] || fail "O .deb contém recursos exclusivos de Windows."

unpack="$(mktemp -d "$stage/unpack.XXXXXX")"
dpkg-deb --extract "$deb" "$unpack"
binary="$(find "$unpack/usr/bin" -maxdepth 1 -type f -name ziggs-companion -print -quit)"
[[ -n "$binary" ]] || fail "Binário principal ausente no .deb."
binary_info="$(file -b "$binary")"
case "$binary_info" in
  *"ELF 64-bit"*"x86-64"*) ;;
  *) fail "O binário empacotado não é ELF x86-64." ;;
esac

missing_libraries=0
while IFS= read -r line; do
  case "$line" in
    *"not found"*)
      printf 'Biblioteca ausente: %s\n' "$line" >&2
      missing_libraries=1
      ;;
  esac
done < <(ldd "$binary")
[[ "$missing_libraries" -eq 0 ]] || fail "O binário possui bibliotecas ausentes."

artifact_dir="$HOME/artifacts/ziggs-companion/v${version}/linux-amd64/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$artifact_dir"
cp -- "$deb" "$signature" "$artifact_dir/"
(
  cd "$artifact_dir"
  sha256sum "$(basename "$deb")" > SHA256SUMS
)

printf '\nBuild assinada concluída.\n'
printf 'Artefatos locais: %s\n' "$artifact_dir"
printf 'Pacote: %s\n' "$(basename "$deb")"
printf 'Assinatura: %s\n' "$(basename "$signature")"
printf '%s\n' 'Nenhum artefato foi publicado.'
