#!/bin/sh
# Sign (and optionally notarise) PsionNet.app.
#
# Identity is chosen automatically:
#   1. "Developer ID Application"  -- required for distributing to other Macs
#   2. "Apple Development"         -- fine locally, still blocked by Gatekeeper elsewhere
#   3. ad-hoc "-"                  -- last resort
#
# Notarisation needs a Developer ID certificate AND stored credentials:
#   xcrun notarytool store-credentials notarytool \
#         --apple-id <your-apple-id> --team-id <your-team-id> --password <app-specific-password>
set -e
cd "$(dirname "$0")/.."
APP=PsionNet.app
ENT=build_icon/entitlements.plist
[ -d "$APP" ] || { echo "$APP not found -- run bin/build-app.sh first"; exit 1; }

# Select by SHA-1 hash, not by name: duplicate certificates with identical
# names are common after renewing, and codesign then fails with
# "ambiguous (matches multiple identities)".
# Read the team from the certificate rather than hardcoding one, so the
# script works for whoever builds it.
TEAM=$(security find-identity -v -p codesigning 2>/dev/null \
       | grep -F "Developer ID Application" | head -1 \
       | sed -n 's/.*(\([A-Z0-9]\{10\}\)).*/\1/p')
[ -n "$TEAM" ] || TEAM="<your-team-id>"

pick() {
  security find-identity -v -p codesigning 2>/dev/null \
    | grep -F "$1" | head -1 | awk '{print $2}'
}
describe() {
  security find-identity -v -p codesigning 2>/dev/null \
    | grep -F "$1" | head -1 | sed 's/.*"\(.*\)"/\1/'
}
ID=$(pick "Developer ID Application"); NAME=$(describe "Developer ID Application")
KIND=devid
[ -n "$ID" ] || { ID=$(pick "Apple Development"); NAME=$(describe "Apple Development"); KIND=dev; }
[ -n "$ID" ] || { ID="-"; NAME="ad-hoc"; KIND=adhoc; }
echo "Signing with: $NAME"
echo "          id: $ID"

# Sign inner code first, then the bundle. --deep is deprecated and does not
# apply entitlements to nested code, so walk it explicitly.
#
# Detect Mach-O by CONTENT, not by extension: PyInstaller ships the Tcl and Tk
# framework binaries as Contents/Frameworks/{Tcl,Tk} with no suffix at all, and
# an *.so/*.dylib glob silently skips them. Notarisation then fails with
# "binary is not signed with a valid Developer ID certificate".
echo "Signing nested binaries..."
COUNT=0
while IFS= read -r f; do
  case "$(file -b "$f" 2>/dev/null)" in
    *Mach-O*)
      codesign -f -s "$ID" --timestamp --options runtime "$f" 2>/dev/null && \
        COUNT=$((COUNT+1))
      ;;
  esac
done <<EOF
$(find "$APP" -type f ! -path "*/Contents/MacOS/PsionNet")
EOF
echo "  signed $COUNT nested binaries"

# Framework bundles get signed as bundles, deepest first.
find "$APP" -type d -name '*.framework' 2>/dev/null | sort -r \
  | while read -r fw; do
      codesign -f -s "$ID" --timestamp --options runtime "$fw" 2>/dev/null || true
    done

if [ "$KIND" = adhoc ]; then
  codesign -f -s - "$APP"
else
  codesign -f -s "$ID" --timestamp --options runtime --entitlements "$ENT" "$APP"
fi

codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -3
echo
if [ "$KIND" != devid ]; then
  echo "NOTE: this is not a Developer ID certificate, so other Macs will still"
  echo "      challenge the app. Create one in Xcode > Settings > Accounts >"
  echo "      Manage Certificates > + > Developer ID Application, then re-run."
  exit 0
fi

if [ "$1" != "--notarize" ]; then
  echo "Signed with Developer ID. Add --notarize to submit to Apple."
  spctl -a -vv "$APP" 2>&1 | sed 's/^/  /'
  exit 0
fi

# Two ways to authenticate. Either is fine; the API key is revocable and
# scoped, which is the nicer option.
AUTH=""
if xcrun notarytool history --keychain-profile notarytool >/dev/null 2>&1; then
  AUTH="--keychain-profile notarytool"
  echo "Using stored keychain profile 'notarytool'."
else
  KEY=$(ls "$HOME/.appstoreconnect/private_keys"/AuthKey_*.p8 2>/dev/null | head -1)
  if [ -n "$KEY" ] && [ -n "$NOTARY_KEY_ID" ] && [ -n "$NOTARY_ISSUER" ]; then
    AUTH="--key $KEY --key-id $NOTARY_KEY_ID --issuer $NOTARY_ISSUER"
    echo "Using App Store Connect API key $(basename "$KEY")."
  fi
fi

if [ -z "$AUTH" ]; then
  cat <<'HELP'

Cannot notarise: no credentials found. Set up ONE of these, then re-run.

  A. App-specific password (simplest)
     Create one at appleid.apple.com > Sign-In and Security >
     App-Specific Passwords -- this is NOT your Apple ID password. Then:

       xcrun notarytool store-credentials notarytool \
         --apple-id <your-apple-id> --team-id $TEAM --password <app-specific-password>

  B. App Store Connect API key (revocable, scoped -- preferred)
     appstoreconnect.apple.com > Users and Access > Integrations > Keys,
     role "Developer". Download AuthKey_XXXX.p8 to
     ~/.appstoreconnect/private_keys/ then:

       export NOTARY_KEY_ID=XXXXXXXXXX
       export NOTARY_ISSUER=<issuer-uuid>
       sh bin/sign-app.sh --notarize
HELP
  exit 1
fi

echo "Submitting for notarisation (a few minutes)..."
ZIP=$(mktemp -d)/PsionNet.zip
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" $AUTH --wait
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl -a -vv "$APP" 2>&1 | sed 's/^/  /'
echo "Notarised and stapled -- opens cleanly on any Mac."
