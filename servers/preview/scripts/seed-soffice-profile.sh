#!/bin/sh
set -eu

destination=$1
soffice_bin=$(command -v "${2:-${UFO_PREVIEW_SOFFICE_BIN:-soffice}}")
destination_parent=$(dirname "$destination")
mkdir -p "$destination_parent"
destination_parent=$(cd "$destination_parent" && pwd -P)
profile="$destination_parent/$(basename "$destination")"
test ! -e "$profile"
profile_seed=$(mktemp -d "$destination_parent/.soffice-profile.XXXXXX")
profile_work=$(mktemp -d)
cleanup() {
    rm -rf "$profile_work"
    if [ -n "${profile_seed:-}" ]; then
        rm -rf "$profile_seed"
    fi
}
trap cleanup EXIT
printf '<!doctype html><html><body>profile</body></html>' > "$profile_work/profile.html"
env -i HOME="$profile_work" "$soffice_bin" \
    --headless \
    --norestore \
    "-env:UserInstallation=file://$profile_seed" \
    --convert-to pdf \
    --outdir "$profile_work" \
    "$profile_work/profile.html"
test -s "$profile_work/profile.pdf"
test -s "$profile_seed/user/extensions/buildid"
profile_buildid=$(cat "$profile_seed/user/extensions/buildid")
soffice_version=$("$soffice_bin" --version)
printf '%s\n' "$soffice_version" > "$profile_seed/.ufo-preview-version"
printf '%s\n' "$soffice_version" | grep -Fq "$profile_buildid"
grep -q 'oor:name="ooSetupLastVersion"' "$profile_seed/user/registrymodifications.xcu"
test -z "$(find "$profile_seed" ! -type d ! -type f -print -quit)"
chmod -R a+rX "$profile_seed"
mv "$profile_seed" "$profile"
profile_seed=
