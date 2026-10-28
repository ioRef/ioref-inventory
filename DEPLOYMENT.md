# Inventory deployment

This guide assumes a production deployment of ioRef Inventory and follows the
shared model described in
[Application deployment](https://github.com/ioref/ioref-infrastructure/blob/main/docs/DEPLOYMENT.md).
Before using it, complete
[Host setup](https://github.com/ioref/ioref-infrastructure/blob/main/docs/HOST_SETUP.md)
and obtain the production configuration, secrets, DNS records, TLS
certificate, deployment host access, and access to the repository's GitHub
Actions settings.

## Deployment model

Inventory runs as a rootless Podman container under the locked `deploy`
account and is managed by a Quadlet-generated systemd user service. Its
repository-specific GitHub Actions runner deploys an image from
`ghcr.io/ioref/ioref-inventory` tagged for the selected commit. Configuration
and secrets remain outside the container in a root-owned environment file
readable by `deploy`, while the SQLite database persists in a named Podman
volume.

Apache terminates TLS and proxies public requests to Inventory over its
loopback-only port. ioRef Web reaches the Inventory API over the shared
rootless Podman network.

```mermaid
swimlane-beta TB
    subgraph Maintainer ["Maintainer"]
        push[Push code to main]
        dispatch["Start deployment workflow<br/><br/><i>Select a specific build commit hash, or leave blank for most recent</i>"]
        review[Review result]
    end

    subgraph GitHub ["GitHub"]
        build[Build Inventory container image]
        publish[Tag image for the commit and publish it to GHCR]
        resolve[Resolve selected commit or use most recent]
        queue[Queue Inventory production deployment job]
        report[Report result]
    end

    subgraph Runner ["Deployment host: Local GitHub runner"]
        pickup[Pick up queued job]
        requestRestart[Ask systemd to restart Inventory]
        health{Inventory health check passes}
        requestRollback[Request rollback]
    end

    subgraph Runtime ["Deployment host: systemd and Podman"]
        record[Note current production image]
        pull[Pull selected Inventory image]
        retag[Tag selected image as production]
        restart[Restart ioref-inventory-production]
        container[Run rootless Inventory container]
        running[Keep new container running]
        rollback[Restore previous image and restart service]
    end

    push -->|Starts build workflow| build --> publish
    push ~~~ dispatch
    dispatch -->|Workflow dispatched| resolve --> queue
    queue -->|Job available| pickup
    pickup --> record --> pull --> retag --> requestRestart
    requestRestart --> restart --> container --> health
    health -->|Yes| running --> report
    health -->|No| requestRollback --> rollback --> report
    report --> review
```

## Resource names

Inventory production uses these names and locations:

| Resource | Name or location |
|---|---|
| Repository | `ioref-inventory` |
| Container and service | `ioref-inventory-production` |
| Local image | `localhost/ioref-inventory:production` |
| Environment file | `/etc/ioref-inventory/production.env` |
| Quadlet | `/home/deploy/.config/containers/systemd/ioref-inventory-production.container` |
| Environment network | `/home/deploy/.config/containers/systemd/ioref-production.network` |
| Persistent volume | `ioref-inventory-production-data` |
| Runner directory | `/opt/github-actions-runner-inventory` |
| Required runner label | `ioref-inventory-production` |

---

# 1. Production configuration

```bash
install -d -o root -g deploy -m 0750 /etc/ioref-inventory
install -o root -g deploy -m 0640 /dev/null /etc/ioref-inventory/production.env
```

Edit `/etc/ioref-inventory/production.env`, using `.env.example` as the
reference for every available setting. Generate a unique secret key on the
production server itself. Use a separate key per environment, never committed,
never routinely rotated:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(50))'
```

```dotenv
SECRET_KEY=...
DEBUG=False
ALLOWED_HOSTS=inventory.ioref.org
CSRF_TRUSTED_ORIGINS=https://inventory.ioref.org

PUBLIC_BROWSE=True
TIME_ZONE=America/New_York
LOG_LEVEL=INFO
SECURE_PROXY=True

AUTH_MODE=shib

REMOTE_USER_HEADER=HTTP_EPPN
REMOTE_USER_EMAIL_HEADER=HTTP_MAIL
REMOTE_USER_NAME_HEADER=HTTP_DISPLAYNAME
LOGIN_URL=/Shibboleth.sso/Login

STOCK_REPORT_EMAIL=rzachari@andrew.cmu.edu,help@ideate.cmu.edu
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=relay.andrew.cmu.edu
EMAIL_PORT=25
EMAIL_TIMEOUT=10
DEFAULT_FROM_EMAIL=stock-report@ioref.org
```

`SCRIPT_NAME` is unset because this application owns its hostname. Stock
reports filed from ioref-web are emailed to each address in `STOCK_REPORT_EMAIL`, comma-separated, through CMU's
relay, which accepts mail from this host without authentication; ioref.org's
SPF record names CMU's mail servers so the sender passes.

The `deploy` user can read this file but cannot modify it.

---

# 2. Quadlet and persistent volume

Confirm that host setup created the production network:

```bash
test -f /home/deploy/.config/containers/systemd/ioref-production.network
```

Applications on this network can reach Inventory without sending same-host
traffic through Apache or an external proxy.

```bash
touch /home/deploy/.config/containers/systemd/ioref-inventory-production.container
```

```ini
[Unit]
Description=ioref Inventory Production
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=ioref-inventory-production
Image=localhost/ioref-inventory:production
EnvironmentFile=/etc/ioref-inventory/production.env
Network=ioref-production.network
NetworkAlias=inventory.ioref.org
Volume=ioref-inventory-production-data:/app/data
PublishPort=127.0.0.1:8000:8000
HealthCmd=python -c "import os,urllib.request,sys; p=os.environ.get('SCRIPT_NAME',''); host=os.environ.get('ALLOWED_HOSTS','').split(',')[0].strip() or '127.0.0.1'; req=urllib.request.Request(f'http://127.0.0.1:8000{p}/api/v1/health/', headers={'Host': host}); sys.exit(0 if urllib.request.urlopen(req).status==200 else 1)"
HealthInterval=30s
HealthTimeout=5s
HealthStartPeriod=10s
HealthRetries=3

[Service]
Restart=on-failure
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

**The healthcheck is defined here, not read from the image.** The Dockerfile's own `HEALTHCHECK` instruction only takes effect for a local `docker build`; the build workflow pushes a plain OCI image to GHCR, and the OCI Image Spec has no field for Docker's `HEALTHCHECK` at all, so it's silently absent from every image this pipeline has ever built. `deploy.yml`'s `podman healthcheck run` has nothing to run without these lines.

```bash
chown deploy:deploy \
  /home/deploy/.config/containers/systemd/ioref-inventory-production.container
chmod 0644 \
  /home/deploy/.config/containers/systemd/ioref-inventory-production.container

sudo -iu deploy podman volume create ioref-inventory-production-data

sudo -u deploy XDG_RUNTIME_DIR=/run/user/$(id -u deploy) \
  systemctl --user daemon-reload
```

The Quadlet mounts this volume at `/app/data`, persisting independently of
the container. Do not start the service yet. The local
`localhost/ioref-inventory:production` image is created by the first
deployment.

## Moving an existing SQLite database into the volume

If a database from elsewhere is being moved onto this host, copy it into the
volume before the first deployment.

Find the volume's host path:

```bash
sudo -iu deploy podman volume inspect ioref-inventory-production-data \
  --format '{{ .Mountpoint }}'
```

The application runs inside the container as the `ioref` user; find its
numeric UID and GID:

```bash
sudo -iu deploy podman run --rm \
  --entrypoint id \
  localhost/ioref-inventory:production \
  ioref
```

Inspect the rootless Podman UID and GID mappings:

```bash
sudo -iu deploy podman unshare cat /proc/self/uid_map
sudo -iu deploy podman unshare cat /proc/self/gid_map
```

For example, with `ioref` at UID 1001/GID 999 and subordinate mappings
beginning at 100000, container UID 1001 maps to host UID 101000 and
container GID 999 maps to host GID 100998.

As root, copy the database in and set both it and its containing directory
to the mapped UID and GID (use the values calculated above, not these
examples):

```bash
VOL=/home/deploy/.local/share/containers/storage/volumes/ioref-inventory-production-data/_data

cp /path/to/db.sqlite3 "$VOL/db.sqlite3"

chown 101000:100998 "$VOL"
chown 101000:100998 "$VOL/db.sqlite3"
chmod 0640 "$VOL/db.sqlite3"
```

The directory must be writable by the container's `ioref` user because
SQLite creates journal and WAL sidecar files beside `db.sqlite3`.

---

# 3. Configure Apache and Shibboleth

The container listens on `127.0.0.1:8000`; nothing outside the host can
reach it. Apache terminates TLS, runs the Shibboleth service provider, and
proxies to it. The shared [Apache conventions](https://github.com/ioref/ioref-infrastructure/blob/main/docs/DEPLOYMENT.md#apache-reverse-proxy)
cover proxying order, `X-Forwarded-Proto`, and TLS; this section covers what's
specific to this application, all of it driven by one fact:

**This vhost is the only thing standing between `TrustedHeaderBackend` and
anyone who can reach the host.** Under `AUTH_MODE=shib` the application
trusts its identity headers without verification, so the web tier has two
jobs of equal weight: pass the authentic identity through, and strip any the
client sent for itself.

### The files

| File | Owner | Purpose |
|---|---|---|
| `/etc/httpd/conf.d/01-shib.conf` | shibboleth RPM | Loads `mod_shib`, exposes `/Shibboleth.sso` |
| `/etc/httpd/conf.d/02-ioref-web.conf` | ioref-web | `ioref.org`, and the default `*:443` vhost for unmatched names |
| `/etc/httpd/conf.d/03-ioref-inventory.conf` | this application | `inventory.ioref.org` |

The numbering is load order, and it matters twice: both vhosts use Shibboleth
directives, so they must load after `01-shib.conf`, and ioref-web's must load
first so its redirect block stays the default for names nothing else claims.

Leave `01-shib.conf` as the package installed it; it is preserved across
upgrades, and its `<Location /secure>` block is the stock example and
unused. One setting in it governs the syntax below: `ShibCompatValidUser
Off` means `Require valid-user` does **not** accept a Shibboleth session,
because `mod_shib` leaves `r->user` unset and `mod_authz_user` then denies
everyone. Session requirements are written `Require shib-session` instead.

### Registered assertion consumer endpoints

CMU has registered five, four on the apex and one on the inventory hostname:

| Index | Binding | Location |
|---|---|---|
| 1 | HTTP-POST | `https://ioref.org/Shibboleth.sso/SAML2/POST` |
| 2 | HTTP-POST-SimpleSign | `https://ioref.org/Shibboleth.sso/SAML2/POST-SimpleSign` |
| 3 | HTTP-Artifact | `https://ioref.org/Shibboleth.sso/SAML2/Artifact` |
| 4 | PAOS | `https://ioref.org/Shibboleth.sso/SAML2/ECP` |
| 5 | HTTP-POST | `https://inventory.ioref.org/Shibboleth.sso/SAML2/POST` |

There is one service provider, entityID `https://ioref.org/shibboleth`, and
its `handlerURL` is relative, so the assertion consumer URL is derived from
the `Host` each request arrives on. A login therefore completes only on a
hostname in that list. This application uses index 5, ioref-web's
stock-report sign-in uses index 1, and `shibboleth2.xml` needs no changes for
either.

`guides.ioref.org` is **not** registered. Logins there are refused by the
IdP with "Web Login Service - Unable to Respond" and nothing is logged
locally, which is why the vhost redirects that name to the apex rather than
serving it.

### Stripping client-supplied identity headers

This block is the security control. Every header the application reads must
appear in it.

```apache
RequestHeader unset Eppn early
RequestHeader unset Mail early
RequestHeader unset Displayname early
RequestHeader unset Remote-User early
RequestHeader unset Persistent-Id early
RequestHeader unset Subject-Id early
RequestHeader unset Pairwise-Id early
```

`accounts/backends.py` trusts these completely, so a client able to send
`Eppn: someone-else@andrew.cmu.edu` authenticates as them. `Eppn` is the one
that matters most: every account has one, and `TrustedHeaderBackend`
resolves on it.

Three permanent-identifier candidates are cleared because `shibboleth2.xml`
sets `REMOTE_USER="eppn subject-id pairwise-id persistent-id"` and which one
CMU releases is not yet established. Unsetting a header nothing reads costs
nothing, and leaves nothing to remember later.

`mod_shib` clears the headers it maps in `attribute-map.xml` on its own, so
this is the second layer, and the only layer for any header an application
reads that the SP does not map. Adding a `REMOTE_USER_*_HEADER` setting in
`config/settings.py` without adding it here reopens the bypass.

**`early` is not optional.** Without it `mod_headers` processes these at the
fixup hook, which runs *after* `mod_shib` has populated the headers from the
SAML session. The unset would then strip the authentic values and break
login rather than block the forgery. `early` moves them to
`post_read_request`, ahead of `mod_shib`. The two failure modes are silent
and opposite, so test both after any change here.

### Access control

```apache
<Location />
  AuthType shibboleth
  ShibRequestSetting requireSession 0
  ShibUseHeaders On
  Require shibboleth
</Location>

<Location /admin>
  AuthType shibboleth
  ShibRequestSetting requireSession 1
  ShibUseHeaders On
  Require shib-session
</Location>

<Location /api>
  AuthType None
  Require all granted
</Location>
```

A lazy session on `/`: the public part pages are anonymous, but a
signed-in visitor is recognized. `Require shibboleth` is the provider that
permits anonymous access, where `shib-session` would demand one and lock the
public views. `ShibUseHeaders` is scoped per location rather than
vhost-wide: `mod_shib` exposes attributes as environment variables by
default, which a reverse-proxied application cannot see, and headers are
what survive the hop to gunicorn.

`/admin` requires a live CMU session but not a particular person.
Authentication is Apache's job and authorization is Django's, through
`is_staff`. `TrustedHeaderBackend` creates no accounts, so an eppn with no
row resolves to nobody and is sent away as an anonymous visitor without
anything being written.

`/api` stays outside Shibboleth because ioref-web is a service and
cannot complete a browser redirect to an identity provider. DRF enforces
bearer keys there instead.

### Proxying for this application

```apache
ProxyPass        /Shibboleth.sso !

ProxyPass        / http://127.0.0.1:8000/
ProxyPassReverse / http://127.0.0.1:8000/
```

The `/Shibboleth.sso` exclusion comes first, or the service provider's own
handler is proxied into the application.

A deployment that mounts this application under a path instead sets
`SCRIPT_NAME` and must proxy the prefix **intact**, with no trailing slash on
either side: `ProxyPass /prefix http://127.0.0.1:8000/prefix`. gunicorn strips
the prefix itself and rejects any request whose path does not start with it,
so mapping `/prefix/` to `/`, the usual reverse-proxy reflex, produces a 500
on every request.

### Verifying

```bash
apachectl configtest
systemctl reload httpd
```

A forged header must not authenticate:

```bash
curl -sS -H 'Eppn: nobody@andrew.cmu.edu' https://inventory.ioref.org/ \
  | grep -ci 'sign out'      # expect 0
```

A real login must still work: sign in through a browser and confirm
`https://inventory.ioref.org/admin/` is reachable. If the forgery test
passes but real logins break, `early` is missing or misplaced.

The same request against gunicorn directly, from the host, **does**
authenticate:

```bash
curl -sS -H 'Eppn: you@andrew.cmu.edu' http://127.0.0.1:8000/
```

That is expected. It is why `PublishPort` binds to `127.0.0.1` and why
nothing else may be allowed to reach port 8000. The header scrubbing and the
loopback binding are two halves of one control, and neither is sufficient
alone.

---

# 4. Install the GitHub Actions runner

Follow the shared [runner installation guide](https://github.com/ioref/ioref-infrastructure/blob/main/docs/DEPLOYMENT.md#github-actions-runner) with:

```text
Repository:        ioref/ioref-inventory
Directory:          /opt/github-actions-runner-inventory
Label:               ioref-inventory-production
```

---

# 5. First deployment

1. Confirm the desired commit has passed CI (**Build image**).
2. Confirm CI produced `ghcr.io/ioref/ioref-inventory:sha-COMMIT-SHA`.
3. In GitHub Actions, select **Deploy production**, choose the branch or
   commit, and click **Run workflow**.

Verify:

```bash
sudo -iu deploy podman images
```

should show both the pulled GHCR image and the local `production` tag.

```bash
sudo -u deploy XDG_RUNTIME_DIR=/run/user/$(id -u deploy) \
  systemctl --user status ioref-inventory-production.service
```

should show it active, and `podman ps --filter name=ioref-inventory-production`
should show `(healthy)`.

---

# Security notes specific to this application

See the infrastructure repository's shared security guidance. Specific to
ioref-inventory:

* `AUTH_MODE=shib` trusts its request headers unconditionally, and the
  Apache header-stripping block above is the only thing preventing identity
  forgery. Treat any change to that block, or to which headers
  `config/settings.py` reads, as a security-relevant change requiring the
  same review as a change to `deploy.yml`.
* Restrict `/etc/ioref-inventory/production.env` to root and the `deploy`
  group.
