#!/bin/bash
# Ensure shelluser home dir exists when /home is a volume mount
if [ ! -d /home/shelluser ]; then
    mkdir -p /home/shelluser
fi
if [ ! -d /home/shelluser/.ssh ]; then
    mkdir -p /home/shelluser/.ssh
    chmod 700 /home/shelluser/.ssh
fi
chown -R shelluser:shelluser /home/shelluser

exec /usr/sbin/sshd -D -e
