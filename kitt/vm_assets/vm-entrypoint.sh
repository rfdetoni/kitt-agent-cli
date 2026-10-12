#!/bin/sh
# Offline QEMU guest inside Docker/Podman with no guest NIC.
set -eu
umask 077
: "${KITT_GUEST_OS:?}"
: "${KITT_SOURCE_KIND:?}"
: "${KITT_MEMORY_MIB:?}"
: "${KITT_CPUS:?}"
: "${KITT_DISK_GIB:?}"
: "${KITT_INSTALL:?}"
case "$KITT_GUEST_OS" in windows|linux) ;; *) exit 2;; esac
case "$KITT_SOURCE_KIND" in iso|qcow2) ;; *) exit 2;; esac
case "$KITT_INSTALL" in 0|1) ;; *) exit 2;; esac
case "$KITT_MEMORY_MIB:$KITT_CPUS:$KITT_DISK_GIB" in
  *[!0123456789:]*|"") exit 2 ;;
esac
if [ "$KITT_SOURCE_KIND" = qcow2 ] && [ "$KITT_INSTALL" = 1 ]; then
  echo "Existing QCOW2 cannot use ISO install mode" >&2
  exit 2
fi
if [ ! -f /state/disk.qcow2 ]; then
  if [ "$KITT_SOURCE_KIND" = qcow2 ]; then
    echo "Imported QCOW2 disk missing" >&2
    exit 2
  fi
  qemu-img create -f qcow2 /state/disk.qcow2 "${KITT_DISK_GIB}G"
fi
qemu-img info --output=json /state/disk.qcow2 >/dev/null
set -- -nodefaults -no-user-config -machine q35,accel=kvm:tcg \
  -cpu max -m "$KITT_MEMORY_MIB" -smp "$KITT_CPUS" \
  -display none -vnc 127.0.0.1:1 -monitor none -serial none -vga std \
  -device qemu-xhci,id=xhci -device usb-kbd,bus=xhci.0 \
  -device usb-tablet,bus=xhci.0 \
  -drive file=/state/disk.qcow2,format=qcow2,if=ide,index=0,media=disk
if [ "${KITT_PROBE_SSH:-0}" = 1 ]; then
  # Only host -> guest SSH. QEMU user networking blocks guest egress.
  set -- "$@" -netdev user,id=kitt-probe,restrict=on,ipv6=off,hostfwd=tcp:127.0.0.1:2222-:22 \
    -device e1000,netdev=kitt-probe
else
  set -- "$@" -nic none
fi
if [ "$KITT_SOURCE_KIND" = iso ] && [ "$KITT_INSTALL" = 1 ]; then
  test -f /input/installer.iso || { echo "User ISO missing" >&2; exit 2; }
  set -- "$@" -boot order=d \
    -drive file=/input/installer.iso,format=raw,if=ide,index=1,media=cdrom,readonly=on
fi
cleanup() {
  if [ -n "${WEBSOCKIFY_PID:-}" ]; then kill "$WEBSOCKIFY_PID" 2>/dev/null || :; fi
  if [ -n "${NGINX_PID:-}" ]; then kill "$NGINX_PID" 2>/dev/null || :; fi
  if [ -n "${TPM_PID:-}" ]; then kill "$TPM_PID" 2>/dev/null || :; fi
}
trap cleanup EXIT INT TERM
if [ "$KITT_GUEST_OS" = windows ]; then
  for candidate in /usr/share/OVMF/OVMF_CODE_4M.fd \
                   /usr/share/OVMF/OVMF_CODE_4M.secboot.fd \
                   /usr/share/OVMF/OVMF_CODE.fd; do
    if [ -r "$candidate" ]; then CODE="$candidate"; break; fi
  done
  for candidate in /usr/share/OVMF/OVMF_VARS_4M.fd \
                   /usr/share/OVMF/OVMF_VARS_4M.ms.fd \
                   /usr/share/OVMF/OVMF_VARS.fd; do
    if [ -r "$candidate" ]; then VARS="$candidate"; break; fi
  done
  test -n "${CODE:-}" && test -n "${VARS:-}" ||
    { echo "OVMF firmware unavailable" >&2; exit 2; }
  if [ ! -f /state/OVMF_VARS.fd ]; then cp "$VARS" /state/OVMF_VARS.fd; fi
  mkdir -p /state/tpm
  swtpm socket --tpm2 --tpmstate dir=/state/tpm \
    --ctrl type=unixio,path=/state/tpm.sock --flags startup-clear &
  TPM_PID=$!
  tries=0
  while [ ! -S /state/tpm.sock ]; do
    tries=$((tries+1))
    test "$tries" -lt 50 || { echo "TPM startup timed out" >&2; exit 2; }
    sleep .1
  done
  set -- "$@" \
    -drive if=pflash,format=raw,readonly=on,file="$CODE" \
    -drive if=pflash,format=raw,file=/state/OVMF_VARS.fd \
    -chardev socket,id=chrtpm,path=/state/tpm.sock \
    -tpmdev emulator,id=tpm0,chardev=chrtpm \
    -device tpm-tis,tpmdev=tpm0
fi
test -r /run/secrets/kitt-vnc.htpasswd ||
  { echo "Authenticated noVNC credentials are required" >&2; exit 2; }
websockify --web=/usr/share/novnc 127.0.0.1:8007 127.0.0.1:5901 &
WEBSOCKIFY_PID=$!
nginx -c /etc/nginx/kitt-vm.conf -g 'daemon off;' &
NGINX_PID=$!
qemu-system-x86_64 "$@"
