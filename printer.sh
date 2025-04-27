#!/bin/bash

# creality_ender3v3

# Config
PRINTER_IP="192.168.68.55"
PRINTER_USER="root"
SSH_KEY="$HOME/.ssh/id_rsa"
REMOTE_UPLOAD_PATH="/usr/data/printer_data/gcodes"

# Check if user provided a file
GCODE_FILE="$1"

# Global variables for temp files
TEMP_FILE=""
TEMP_FILE2=""
FINAL_FILE=""

function setup_ssh_key() {
    LOCAL_PUBLIC_KEY="$HOME/.ssh/id_rsa.pub"

    if [[ ! -f "$LOCAL_PUBLIC_KEY" ]]; then
        echo "❌ No public SSH key found at $LOCAL_PUBLIC_KEY."
        echo "Generating a new SSH key pair..."
        ssh-keygen -t rsa -b 4096 -f "$HOME/.ssh/id_rsa" -N ""
    fi

    echo "Copying SSH public key to the printer..."

    ssh "$PRINTER_USER@$PRINTER_IP" '
        mkdir -p ~/.ssh
        chmod 700 ~/.ssh
    '

    cat "$LOCAL_PUBLIC_KEY" | ssh "$PRINTER_USER@$PRINTER_IP" '
        cat >> ~/.ssh/authorized_keys
        chmod 600 ~/.ssh/authorized_keys
    '

    echo "✅ SSH key successfully installed!"
}

function check_status() {
    echo "Connecting to 3D printer at $PRINTER_IP to check status..."
    STATUS=$(ssh -i "$SSH_KEY" "$PRINTER_USER@$PRINTER_IP" '
        if systemctl is-active --quiet klipper; then
            echo "Printing"
        else
            echo "Idle"
        fi
    ' 2>/dev/null)

    if [[ -z "$STATUS" ]]; then
        echo "❌ Failed to connect to printer or retrieve status."
        exit 1
    fi

    echo "🖨️ Printer Status: $STATUS"
}



function upload_and_print() {
    if [[ ! -f "$GCODE_FILE" ]]; then
        echo "❌ File not found: $GCODE_FILE"
        exit 1
    fi

    BASENAME=$(basename "$GCODE_FILE")

    # Decide which file to upload
    local FILE_TO_UPLOAD="$GCODE_FILE"
    if [[ -n "$FINAL_FILE" ]]; then
        FILE_TO_UPLOAD="$FINAL_FILE"
    fi

    echo "Uploading $FILE_TO_UPLOAD to printer..."

    cat "$FILE_TO_UPLOAD" | ssh -i "$SSH_KEY" "$PRINTER_USER@$PRINTER_IP" "cat > '$REMOTE_UPLOAD_PATH/$BASENAME'"

    if [[ $? -ne 0 ]]; then
        echo "❌ Failed to upload file."
        exit 1
    fi

    echo "Starting print of $BASENAME..."
    ssh -i "$SSH_KEY" "$PRINTER_USER@$PRINTER_IP" "
        curl -X POST -H 'Content-Type: application/json' \
        -d '{\"filename\": \"$BASENAME\"}' \
        http://localhost:7125/printer/print/start
    "

    # Cleanup temp files
    rm -f "$TEMP_FILE" "$TEMP_FILE2"
}

# Script logic
check_status

if [[ -n "$GCODE_FILE" ]]; then
    upload_and_print
fi

