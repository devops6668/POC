#!/bin/bash
# patch_kpack_images_v2.sh
# Patch all kpack Image resources in a namespace with:
#   successBuildHistoryLimit: 4
#   failedBuildHistoryLimit: 4
#
# Usage:
#   ./patch_kpack_images_v2.sh [namespace]
#
# Without args: prompts for namespace interactively.
# With args: patches the specified namespace directly.

set -euo pipefail

# --- namespace ---
if [[ $# -ge 1 ]]; then
  NS="$1"
else
  read -rp "Enter Namespace: " NS
fi

if [[ -z "$NS" ]]; then
  echo "Error: Namespace cannot be empty."
  exit 1
fi

# --- discover images ---
echo "Scanning kpack Image resources in namespace: $NS"

IMAGES=$(kubectl get image -n "$NS" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null) || true

if [[ -z "$IMAGES" ]]; then
  echo "No kpack Image resources found in $NS"
  exit 0
fi

# --- patch ---
PATCHED=0
FAILED=0

for IMG in $IMAGES; do
  echo -n "Patching $NS/$IMG ... "
  if kubectl patch image "$IMG" -n "$NS" \
    -p '{"spec":{"successBuildHistoryLimit":3,"failedBuildHistoryLimit":3}}' \
    --type=merge 2>/dev/null; then
    echo "OK"
    PATCHED=$((PATCHED + 1))
  else
    echo "FAILED"
    FAILED=$((FAILED + 1))
  fi
done

echo ""
echo "Done. Patched: $PATCHED, Failed: $FAILED"
