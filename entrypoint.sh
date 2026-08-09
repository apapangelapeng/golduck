#!/bin/bash
# cgroupv2
mkdir /sys/fs/cgroup/JAIL
echo "0" > /sys/fs/cgroup/JAIL/cgroup.procs
cat /proc/self/cgroup
echo "+pids +memory +cpu" > /sys/fs/cgroup/cgroup.subtree_control && chown user:user /sys/fs/cgroup && chown user:user /sys/fs/cgroup/cgroup.procs || true

all_caps="-cap_0"
for i in $(seq 1 $(cat /proc/sys/kernel/cap_last_cap)); do
  all_caps+=",-cap_${i}"
done

exec setpriv --init-groups --reset-env --reuid user --regid user --inh-caps=${all_caps} -- "$@"
