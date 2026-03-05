#!/bin/bash
AUTH_DIR=/home/.auth

# Ensure auth dir exists
mkdir -p "$AUTH_DIR"

# For each auth file, merge system entries from templates with
# user entries from the PV. System entries (uid < 1000 or 65534)
# come from the baked-in templates; user entries (uid >= 1000)
# come from the PV auth files written by the backend.
for f in passwd shadow group gshadow; do
    template="/etc/shell-templates/$f"
    pv_file="$AUTH_DIR/$f"
    tmp="/tmp/merged_$f"

    # Start with system entries from template
    cp "$template" "$tmp"

    # Append user entries from PV if present
    if [ -f "$pv_file" ]; then
        while IFS= read -r line; do
            user=$(echo "$line" | cut -d: -f1)
            # Skip if already in template (system user)
            if ! grep -q "^${user}:" "$template"; then
                echo "$line" >> "$tmp"
            fi
        done < "$pv_file"
    fi

    cp "$tmp" "$pv_file"
    rm -f "$tmp"
done

chmod 644 "$AUTH_DIR/passwd" "$AUTH_DIR/group"
chmod 640 "$AUTH_DIR/shadow" "$AUTH_DIR/gshadow"

# Symlink /etc auth files to PV so sshd always reads the latest
ln -sf "$AUTH_DIR/passwd" /etc/passwd
ln -sf "$AUTH_DIR/shadow" /etc/shadow
ln -sf "$AUTH_DIR/group" /etc/group
ln -sf "$AUTH_DIR/gshadow" /etc/gshadow

exec /usr/sbin/sshd -D -e
