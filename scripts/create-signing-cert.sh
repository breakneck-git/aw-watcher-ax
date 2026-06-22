#!/bin/bash
# Create a stable, self-signed code-signing identity for aw-watcher-ax.
#
# Why: the Accessibility (TCC) grant is bound to the bundle's code identity.
# An ad-hoc signature (`codesign --sign -`) pins it to the cdhash, which changes
# whenever the trampoline is recompiled with a new toolchain — silently voiding
# the grant. Signing with a real (even self-signed) certificate makes TCC key on
# the certificate-based *designated requirement* instead, so the grant survives
# every rebuild: toolchain bumps AND trampoline.c edits.
#
# Run this ONCE. After it, `install.sh` automatically signs with this identity
# (it falls back to ad-hoc if the identity is absent). Re-grant Accessibility
# one final time after the first cert-signed install; it then sticks for good.
#
# The cert is self-signed and untrusted — that is fine: codesign signs with it
# regardless, and TCC matches the certificate leaf hash, not a trust chain.

set -euo pipefail

CERT_NAME="aw-watcher-ax Code Signing"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
DAYS=18250 # ~50 years, so it never expires out from under future re-signs

if security find-identity -p codesigning 2>/dev/null | grep -q "$CERT_NAME"; then
    echo "Signing identity '$CERT_NAME' already exists — nothing to do."
    exit 0
fi

W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT

cat > "$W/cert.cnf" <<'CNF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = aw-watcher-ax Code Signing
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
CNF

echo "Generating self-signed code-signing certificate (~50y)..."
openssl req -x509 -newkey rsa:2048 -sha256 -days "$DAYS" -nodes \
    -keyout "$W/key.pem" -out "$W/cert.pem" -config "$W/cert.cnf" 2>/dev/null

# macOS `security import` can't read OpenSSL 3.x's default PKCS#12 MAC/cipher;
# emit a legacy (SHA1 + 3DES) container it accepts. The passphrase is transient
# (used only to hand the key to the keychain) — not a secret.
openssl pkcs12 -export -legacy -inkey "$W/key.pem" -in "$W/cert.pem" \
    -name "$CERT_NAME" -out "$W/id.p12" -passout pass:transient \
    -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 2>/dev/null

# -A lets codesign use the private key without a partition-list/ACL prompt.
echo "Importing into the login keychain..."
security import "$W/id.p12" -k "$KEYCHAIN" -P transient -A -T /usr/bin/codesign

echo ""
echo "✓ Created signing identity '$CERT_NAME'."
echo "  Next: run ./install.sh, then re-enable 'aw-watcher-ax' in System Settings →"
echo "  Privacy & Security → Accessibility one last time. The grant then survives"
echo "  all future rebuilds."
