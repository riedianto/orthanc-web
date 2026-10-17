# fail2ban untuk orthanc-auth

Konfigurasi fail2ban (host, bukan container) yang mem-banned IP setelah
10 percobaan login gagal ke `orthanc-auth`, berdasarkan baris `[AUTH FAIL]`
yang ditulis `server.js` ke `worklists/auth.log`.

## Pemasangan

```bash
sudo cp deploy/fail2ban/filter.d/orthanc-auth.conf   /etc/fail2ban/filter.d/
sudo cp deploy/fail2ban/action.d/iptables-docker.conf /etc/fail2ban/action.d/
sudo cp deploy/fail2ban/jail.d/orthanc-auth.local     /etc/fail2ban/jail.d/

# pastikan file log ada agar fail2ban tidak gagal start
sudo touch /simrs/orthanc-web/worklists/auth.log

sudo fail2ban-client reload
sudo fail2ban-client status orthanc-auth
```

## Kenapa action `iptables-docker` (bukan `iptables-multiport`)

Port 8090 dipublish oleh Docker (`ports: 8090:8090`). Trafik masuk ke container
melewati rantai `FORWARD`/`DOCKER-USER`, **bukan** `INPUT`, sehingga ban pada
rantai `INPUT` tidak berpengaruh. Action ini membuat chain `f2b-orthanc-auth`
dan me-referensikannya dari `DOCKER-USER`.

Verifikasi manual:

```bash
sudo iptables -n -L DOCKER-USER | grep f2b-orthanc-auth
sudo fail2ban-client set orthanc-auth banip 203.0.113.55
sudo iptables -n -L f2b-orthanc-auth
sudo fail2ban-client set orthanc-auth unbanip 203.0.113.55
```

## Catatan

- `logpath` hard-coded ke `/simrs/orthanc-web/worklists/auth.log`. Sesuaikan
  bila path instalasi berbeda.
- `maxretry`/`findtime`/`bantime` di sini adalah batas tingkat IP. Rate-limit
  in-app (login lock) dikonfigurasi lewat env `LOGIN_MAX_FAILURES`,
  `LOGIN_WINDOW_MINUTES`, `LOGIN_LOCK_MINUTES` pada service `orthanc-auth`.
