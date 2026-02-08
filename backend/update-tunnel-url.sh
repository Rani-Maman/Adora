#!/bin/bash
# Extract current tunnel URL and save to file

TUNNEL_URL=$(journalctl -u cloudflared -n 200 | grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)

if [ ! -z "$TUNNEL_URL" ]; then
    echo "$TUNNEL_URL" > /tmp/tunnel-url.txt
    chmod 644 /tmp/tunnel-url.txt
    echo "Updated tunnel URL: $TUNNEL_URL"
else
    echo "No tunnel URL found"
fi
