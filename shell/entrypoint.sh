#!/bin/bash
AUTH_DIR=/home/.auth

# Seed auth files from templates if volume is fresh
if [ ! -f "$AUTH_DIR/passwd" ]; then
    mkdir -p "$AUTH_DIR"
    cp /etc/shell-templates/{passwd,shadow,group,gshadow} "$AUTH_DIR/"
    chmod 644 "$AUTH_DIR/passwd" "$AUTH_DIR/group"
    chmod 640 "$AUTH_DIR/shadow" "$AUTH_DIR/gshadow"
fi

# Symlink /etc auth files to PV so sshd always reads the latest
ln -sf "$AUTH_DIR/passwd" /etc/passwd
ln -sf "$AUTH_DIR/shadow" /etc/shadow
ln -sf "$AUTH_DIR/group" /etc/group
ln -sf "$AUTH_DIR/gshadow" /etc/gshadow

exec /usr/sbin/sshd -D -e
