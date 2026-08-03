#!/bin/bash
echo "===== POD 层 ====="
getent hosts github.com >/dev/null 2>&1 && echo "POD_DNS=OK" || echo "POD_DNS=FAIL"
echo "resolv.conf:"; cat /etc/resolv.conf
echo "nsswitch hosts:"; grep -i hosts /etc/nsswitch.conf 2>/dev/null
echo "nameserver 可达?"; for ns in $(awk '/nameserver/{print $2}' /etc/resolv.conf); do
  timeout 3 bash -c "echo>/dev/tcp/$ns/53" 2>/dev/null && echo "  $ns:53 reachable" || echo "  $ns:53 UNREACHABLE"; done
echo
echo "===== 真 chroot 复现(privileged)====="
CR=/tmp/crdiag; rm -rf $CR; mkdir -p $CR
for d in bin sbin lib lib64 usr etc; do [ -e /$d ] && mkdir -p $CR/$d && mount --bind /$d $CR/$d; done
mkdir -p $CR/proc $CR/dev && mount -t proc proc $CR/proc 2>/dev/null; mount --bind /dev $CR/dev 2>/dev/null
echo "chroot 内 resolv.conf:"; chroot $CR cat /etc/resolv.conf 2>&1 | head -3
echo "chroot 内 getent:"; chroot $CR getent hosts github.com >/dev/null 2>&1 && echo "  INCHROOT_DNS=OK" || echo "  INCHROOT_DNS=FAIL"
echo "chroot 内 python 解析:"; chroot $CR python3 -c "import socket;print('resolved',socket.gethostbyname('github.com'))" 2>&1 | tail -1
echo "chroot 内 pip 试探(看真实报错):"; chroot $CR python3 -m pip download --no-deps -d /tmp pytest 2>&1 | tail -3
umount $CR/proc $CR/dev $CR/* 2>/dev/null
