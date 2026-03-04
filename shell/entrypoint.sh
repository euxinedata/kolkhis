#!/bin/bash
AUTH_DIR=/home/.auth

# Seed auth files from templates if volume is fresh
if [ ! -f "$AUTH_DIR/passwd" ]; then
    mkdir -p "$AUTH_DIR"
    cp /etc/shell-templates/{passwd,shadow,group,gshadow} "$AUTH_DIR/"
    chmod 644 "$AUTH_DIR/passwd" "$AUTH_DIR/group"
    chmod 640 "$AUTH_DIR/shadow" "$AUTH_DIR/gshadow"
fi

# Initial copy to /etc/
cp "$AUTH_DIR"/{passwd,shadow,group,gshadow} /etc/

# Background sync: pick up new users added by backend
(while true; do sleep 5; cp "$AUTH_DIR"/{passwd,shadow,group,gshadow} /etc/; done) &

exec /usr/sbin/sshd -D -e
