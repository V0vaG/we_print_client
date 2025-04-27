vi /etc/init.d/S50dropbear
start-stop-daemon -S -q -p /var/run/dropbear.pid --exec /usr/sbin/dropbear -- $DROPBEAR_ARGS
start-stop-daemon -S -q -p /var/run/dropbear.pid --exec /usr/sbin/dropbear -- -R -B

sed -i 's/\$DROPBEAR_ARGS/-R -B/' /etc/init.d/S50dropbear
reboot

