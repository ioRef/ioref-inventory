# Production Deployment

ioref Inventory production runs on **Red Hat Enterprise Linux 9**.

The application runs as a rootless Podman container under the dedicated
`deploy` account. Production configuration is stored in
`/etc/ioref-inventory/production.env`, and persistent application data is
stored in a Podman named volume.

Deployments are initiated manually from GitHub Actions. A repository-scoped
self-hosted GitHub Actions runner on the production server receives deployment
jobs over an outbound connection through `proxy.andrew.cmu.edu:3128`. No
inbound connection from GitHub is required.

## Deployment model

```text
                       GitHub
                         |
            +------------+------------+
            |                         |
       existing CI                GitHub Actions
    builds sha-* image           "Run workflow"
            |                         |
            v                         |
           GHCR                       |
            ^                         |
            | HTTPS through `proxy.andrew.cmu.edu:3128` |
            +-------------------------+
                         |
                  ioref-web-01
                         |
                 deploy user
                         |
             self-hosted runner
                         |
                 rootless Podman
                         |
              ioref-inventory
```

The important separation is:

- **Application image:** built by GitHub CI and stored in GHCR with an immutable
  `sha-<git-sha>` tag.
- **Production configuration and secrets:**
  `/etc/ioref-inventory/production.env`.
- **Persistent application data:** a rootless Podman named volume.
- **Deployment control:** a repository-scoped GitHub Actions runner running as
  `deploy`.
- **Container lifecycle:** a rootless Podman Quadlet managed by the `deploy`
  user's systemd instance.

The deployment workflow pulls an immutable `sha-*` image from GHCR, tags that
image locally as `localhost/ioref-inventory:production`, and restarts the
Quadlet. The mutable `production` tag therefore exists only on the production
host; GHCR remains the source of immutable build artifacts.

---

# One-time host setup

These steps are performed once when preparing a new production server.

These instructions assume **Red Hat Enterprise Linux 9** with systemd and
Podman.

## 1. Install required packages

As root:

```bash
dnf install -y podman curl tar
```

Verify Podman:

```bash
podman --version
```

The web tier is assumed to exist already: `httpd`, `mod_ssl` and
`shibboleth.x86_64`, with the service provider's keypair registered with CMU.
Standing up a new service provider is a coordination with the identity
provider rather than a package install, and its keypair must survive host
rebuilds or the registered metadata goes stale and every login fails. Section 6
covers the configuration that this application requires.

## 2. Create the deployment account

Create a dedicated `deploy` account for the GitHub Actions runner and
rootless Podman containers.

```bash
useradd --create-home --shell /bin/bash deploy
passwd -l deploy
```

The `deploy` account does not need a password or interactive SSH access.

Rootless Podman requires subordinate UID and GID ranges for `deploy`. Check
whether they were allocated when the account was created:

```bash
grep '^deploy:' /etc/subuid
grep '^deploy:' /etc/subgid
```

If both commands return an entry, continue to the Podman test below.

If they return nothing, inspect the ranges already in use:

```bash
cat /etc/subuid
cat /etc/subgid
```

Choose an unused block of 65,536 IDs and assign the same range to `deploy` in
both files. For example, if `100000-165535` is unused:

```bash
usermod --add-subuids 100000-165535 deploy
usermod --add-subgids 100000-165535 deploy
```

Do not reuse a range assigned to another account.

Verify the allocation:

```bash
grep '^deploy:' /etc/subuid
grep '^deploy:' /etc/subgid
```

Both should now show an entry such as:

```text
deploy:100000:65536
```

Test rootless Podman:

```bash
sudo -iu deploy podman info
```

## 3. Keep the deploy user's systemd instance running

Enable lingering so the `deploy` user's systemd instance starts at boot and
continues running without an interactive login:

```bash
loginctl enable-linger deploy
```

The user's runtime directory should then exist at:

```text
/run/user/<deploy-uid>
```

You can obtain the UID with:

```bash
id -u deploy
```

## 4. Create the production configuration file

Create the production configuration directory:

```bash
install -d -o root -g deploy -m 0750 /etc/ioref-inventory
```

Create the environment file:

```bash
install -o root -g deploy -m 0640 /dev/null \
  /etc/ioref-inventory/production.env
```

Edit `/etc/ioref-inventory/production.env` using your preferred editor, using
the project's `.env.example` as the reference for available settings.

Generate a unique Django secret key on the production server:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(50))'
```

Copy the generated value into `production.env`:

```dotenv
SECRET_KEY=<generated-value>
```

Generate a separate key for each environment. Do not commit or routinely
rotate the production key.

`production.env` contains production configuration and secrets. Do not
commit it to Git.

For example:

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
```

Use `.env.example` as the reference for available settings. The `deploy` user
can read the production file but cannot modify it.

## 5. Configure the rootless Podman Quadlet

Create the rootless Quadlet directory:

```bash
install -d -o deploy -g deploy -m 0750 \
  /home/deploy/.config/containers/systemd
```

Create the production Quadlet file:

```bash
touch /home/deploy/.config/containers/systemd/ioref-inventory-production.container
```

Edit `/home/deploy/.config/containers/systemd/ioref-inventory-production.container`
using your preferred editor and add:

```ini
[Unit]
Description=ioref Inventory Production
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=ioref-inventory-production
Image=localhost/ioref-inventory:production
EnvironmentFile=/etc/ioref-inventory/production.env
Volume=ioref-inventory-production-data:/app/data
PublishPort=127.0.0.1:8000:8000

[Service]
Restart=on-failure
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

Set ownership:

```bash
chown deploy:deploy \
  /home/deploy/.config/containers/systemd/ioref-inventory-production.container
chmod 0644 \
  /home/deploy/.config/containers/systemd/ioref-inventory-production.container
```

Create the persistent production data volume:

```bash
sudo -iu deploy podman volume create ioref-inventory-production-data
```

The Quadlet mounts this volume at `/app/data`. It persists independently of the
application container.

If an existing production SQLite database is being moved to this server, copy
it into the volume before the first deployment. Find the volume's host path:

```bash
sudo -iu deploy podman volume inspect ioref-inventory-production-data   --format '{{ .Mountpoint }}'
```

The default SQLite database is `/app/data/db.sqlite3`. If an existing
`db.sqlite3` is being moved to this server, copy it into the volume before the
first deployment.

Find the volume's host path:

```bash
sudo -iu deploy podman volume inspect ioref-inventory-production-data \
  --format '{{ .Mountpoint }}'
```

The application runs inside the container as the `ioref` user. Find its numeric
UID and GID:

```bash
sudo -iu deploy podman run --rm \
  --entrypoint id \
  localhost/ioref-inventory:production \
  ioref
```

Next inspect the rootless Podman UID and GID mappings:

```bash
sudo -iu deploy podman unshare cat /proc/self/uid_map
sudo -iu deploy podman unshare cat /proc/self/gid_map
```

For example, with `ioref` at UID 1001/GID 999 and subordinate mappings beginning
at 100000, container UID 1001 maps to host UID 101000 and container GID 999 maps
to host GID 100998.

As root, copy the existing database into the volume and set both the database
and its containing directory to the mapped UID and GID:

```bash
VOL=/home/deploy/.local/share/containers/storage/volumes/ioref-inventory-production-data/_data

cp /path/to/db.sqlite3 "$VOL/db.sqlite3"

chown 101000:100998 "$VOL"
chown 101000:100998 "$VOL/db.sqlite3"
chmod 0640 "$VOL/db.sqlite3"
```

Use the UID/GID values calculated from the mappings on the actual server rather
than copying the example values above. The directory must be writable by the
container's `ioref` user because SQLite may create journal or WAL files beside
`db.sqlite3`.

Do not start the service yet. The local
`localhost/ioref-inventory:production` image is created by the first deployment.

## 6. Configure Apache and Shibboleth

The container listens on `127.0.0.1:8000` and nothing outside the host can
reach it. Apache terminates TLS, runs the Shibboleth service provider, and
proxies to it.

**This vhost is the only thing standing between `TrustedHeaderBackend` and
anyone who can reach the host.** Under `AUTH_MODE=shib` the application trusts
its identity headers without verification, so the web tier has two jobs of
equal weight: pass the authentic identity through, and strip any the client
sent for itself. It is recorded here because it lives on the host rather than
in this repository, and it is too important to exist in only one place.

### The files

| File | Owner | Purpose |
|---|---|---|
| `/etc/httpd/conf.d/01-shib.conf` | shibboleth RPM | Loads `mod_shib`, exposes `/Shibboleth.sso` |
| `/etc/httpd/conf.d/02-ioref.org.conf` | this host | The `ioref.org` vhost, serving both applications |

Leave `01-shib.conf` as the package installed it; it is preserved across
upgrades, and its `<Location /secure>` block is the stock example and unused.
One setting in it governs the syntax below: `ShibCompatValidUser Off` means
`Require valid-user` does **not** accept a Shibboleth session, because
`mod_shib` leaves `r->user` unset and `mod_authz_user` then denies everyone.
Session requirements are written `Require shib-session` instead.

`02-ioref.org.conf` serves two applications from one vhost:

```
/            ioref-web on 127.0.0.1:8989
/inventory   ioref-inventory on 127.0.0.1:8000
```

It is the shared host's file rather than this application's, but inventory is
mounted inside it, so the two cannot be maintained apart.

It is also the first file in `conf.d` to declare a `VirtualHost`, which makes
its first `*:443` block Apache's default for any name matching no `ServerName`.
That is deliberately a redirect to the canonical name, so `admin.ioref.org`,
`ioref-web-01.andrew.cmu.edu` and `ioref.ideate.cmu.edu` land on `ioref.org`
rather than reaching Django with a `Host` that `ALLOWED_HOSTS` rejects.

### Registered assertion consumer endpoints

CMU has registered five, four on the apex and one on the inventory hostname:

| Index | Binding | Location |
|---|---|---|
| 1 | HTTP-POST | `https://ioref.org/Shibboleth.sso/SAML2/POST` |
| 2 | HTTP-POST-SimpleSign | `https://ioref.org/Shibboleth.sso/SAML2/POST-SimpleSign` |
| 3 | HTTP-Artifact | `https://ioref.org/Shibboleth.sso/SAML2/Artifact` |
| 4 | PAOS | `https://ioref.org/Shibboleth.sso/SAML2/ECP` |
| 5 | HTTP-POST | `https://inventory.ioref.org/Shibboleth.sso/SAML2/POST` |

There is one service provider, entityID `https://ioref.org/shibboleth`, and its
`handlerURL` is relative, so the assertion consumer URL is derived from the
`Host` each request arrives on. A login therefore completes only on a hostname
in that list. Index 1 is what the current deployment at
`https://ioref.org/inventory` uses, and `shibboleth2.xml` needs no changes for
it.

`guides.ioref.org` is **not** registered. Logins there are refused by the IdP
with "Web Login Service - Unable to Respond" and nothing is logged locally,
which is why the vhost redirects that name to the apex rather than serving it.
Index 5 covers `inventory.ioref.org` only.

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
that matters most: every account has one, and `TrustedHeaderBackend` resolves
on it.

Three permanent-identifier candidates are cleared because `shibboleth2.xml`
sets `REMOTE_USER="eppn subject-id pairwise-id persistent-id"` and which one
CMU releases is not yet established. Unsetting a header nothing reads costs
nothing, and leaves nothing to remember later.

`mod_shib` clears the headers it maps in `attribute-map.xml` on its own, so
this is the second layer, and the only layer for any header an application
reads that the SP does not map. Adding a `REMOTE_USER_*_HEADER` setting in
`config/settings.py` without adding it here reopens the bypass.

The directives are vhost-scoped on purpose: ioref-web shares this host and
benefits from the same protection.

**`early` is not optional.** Without it `mod_headers` processes these at the
fixup hook, which runs *after* `mod_shib` has populated the headers from the
SAML session. The unset would then strip the authentic values and break login
rather than block the forgery. `early` moves them to `post_read_request`, ahead
of `mod_shib`.

The two failure modes are silent and opposite, so test both after any change
here.

### Access control

```apache
<Location /inventory>
  AuthType shibboleth
  ShibRequestSetting requireSession 0
  ShibUseHeaders On
  Require shibboleth
</Location>

<Location /inventory/admin>
  AuthType shibboleth
  ShibRequestSetting requireSession 1
  ShibUseHeaders On
  Require shib-session
</Location>

<Location /inventory/api>
  AuthType None
  Require all granted
</Location>
```

A lazy session on `/inventory`: the public part pages are anonymous, but a
signed-in visitor is recognized. `Require shibboleth` is the provider that
permits anonymous access, where `shib-session` would demand one and lock the
public views.

`ShibUseHeaders` is scoped per location rather than vhost-wide. `mod_shib`
exposes attributes as environment variables by default, which a
reverse-proxied application cannot see; headers are what survive the hop to
gunicorn. ioref-web has no use for them.

`/inventory/admin` requires a live CMU session but not a particular person.
Authentication is Apache's job and authorization is Django's, through
`is_staff`. `TrustedHeaderBackend` creates no accounts, so an eppn with no row
resolves to nobody and is sent away as an anonymous visitor without anything
being written. A name allowlist here as well would mean maintaining the roster
in two places, and the copy nobody remembers is the one that locks someone out.

`/inventory/api` stays outside Shibboleth because ioref-web is a service and
cannot complete a browser redirect to an identity provider. DRF enforces bearer
keys there instead.

### Proxying, first match wins

```apache
ProxyPass        /Shibboleth.sso !

ProxyPass        /inventory  http://127.0.0.1:8000/inventory
ProxyPassReverse /inventory  http://127.0.0.1:8000/inventory

ProxyPass        / http://127.0.0.1:8989/
ProxyPassReverse / http://127.0.0.1:8989/
```

Order is load-bearing, and so are the details of the middle pair:

* **The SP handler must never be proxied.** Forwarding it into an application
  is why `/Shibboleth.sso/Session` returns 404 on `admin.ioref.org`.
* **Do not use `RewriteRule [P]` in this vhost.** It ignores `ProxyPass`
  exclusions, which defeats the line above.
* **The prefix is preserved, not stripped.** The container runs gunicorn with
  `SCRIPT_NAME=/inventory` and gunicorn strips it itself, rejecting any request
  whose path does not start with it: `Request path '/' does not start with
  SCRIPT_NAME '/inventory'`. Mapping `/inventory/` to `/` here, the usual
  reverse-proxy reflex, produces exactly that and a 500 on every request.
* **No trailing slashes on either side.** This form matches `/inventory` and
  everything beneath it, so the bare path needs no redirect. With a trailing
  slash `/inventory` would miss and fall through to ioref-web, which answers
  404, and a `RedirectMatch` cannot rescue it because `mod_proxy` claims the
  URL before `mod_alias` runs.
* **ioref-web's `ProxyPass /` must stay last.**

TLS uses `/etc/pki/tls/certs/localhost.crt` with `server-chain.crt`. That is
one InCommon SAN certificate covering `ioref.org`, `guides`, `inventory`,
`admin`, `ioref-web-01.andrew.cmu.edu` and `ioref.ideate.cmu.edu`, expiring
**2026-12-09** and renewed through InCommon rather than certbot. The
`/etc/letsencrypt` path on this host belongs to `04-admin.ioref.org.conf` and
is not what this vhost uses.

`X-Forwarded-Proto` must be set to `https`. Both applications sit behind TLS
terminated here, and ioref-inventory sets `SECURE_PROXY=True`, so without it
Django treats every request as insecure and CSRF origin checks fail.

### Verifying

```bash
apachectl configtest
systemctl reload httpd
```

A forged header must not authenticate:

```bash
curl -sS -H 'Eppn: nobody@andrew.cmu.edu' https://ioref.org/inventory/ \
  | grep -ci 'sign out'      # expect 0
```

A real login must still work: sign in through a browser and confirm
`https://ioref.org/inventory/admin/` is reachable. If the forgery test passes
but real logins break, `early` is missing or misplaced.

The same request against gunicorn directly, from the host, **does**
authenticate:

```bash
curl -sS -H 'Eppn: you@andrew.cmu.edu' http://127.0.0.1:8000/inventory/
```

That is expected. It is why `PublishPort` binds to `127.0.0.1` and why nothing
else may be allowed to reach port 8000. The header scrubbing and the loopback
binding are two halves of one control, and neither is sufficient alone.

### Moving to `inventory.ioref.org`

Index 5 makes this possible but does not require it. On a host or vhost of its
own the mount point disappears: drop `SCRIPT_NAME`, proxy `/` to the app, and
move the three `<Location>` blocks up to `/`, `/admin` and `/api`. Everything
else carries over unchanged, including the reason for `early`.

It also means dropping `FORCE_SCRIPT_NAME` and the derived `STATIC_URL` prefix,
and revisiting the renamed session and CSRF cookies, which exist only to avoid
colliding with ioref-web on a shared hostname. `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` change with it.

Treat it as a planned migration with a rollback rather than a configuration
tweak, since every published `/inventory/...` URL changes.

---

## 7. Install the GitHub Actions runner

Register the runner directly to **ioref/ioref-inventory**.

First create the runner directory:

```bash
mkdir -p /opt/github-actions-runner-inventory
chown deploy:deploy /opt/github-actions-runner-inventory
```

Then switch to the `deploy` account:

```bash
sudo -iu deploy
```

Once the `deploy` shell opens, change into the runner directory:

```bash
cd /opt/github-actions-runner-inventory
```

Set the proxy variables for the bootstrap commands:

```bash
export HTTP_PROXY=http://proxy.andrew.cmu.edu:3128
export HTTPS_PROXY=http://proxy.andrew.cmu.edu:3128
export NO_PROXY=.cmu.edu,.cmu.local,localhost,127.0.0.1
```

These exports are only for downloading and configuring the runner. The runner's
persistent proxy configuration is set in `/opt/github-actions-runner-inventory/.env`
below.

In GitHub:

1. Open **ioref/ioref-inventory**.
2. Open **Settings**.
3. Open **Actions > Runners**.
4. Select **New self-hosted runner**.
5. Choose Linux and the architecture of the production server.

GitHub provides the current download commands and a time-limited registration
token. Use the download, extraction, and configuration commands shown there.

> GitHub's instructions normally begin by creating an `actions-runner`
> directory and changing into it. Skip those `mkdir` and `cd` commands:
> `/opt/github-actions-runner-inventory` already exists and the `deploy` shell should
> already be in that directory.

Run the generated `config.sh` command, using the registration token GitHub
gave you:

```bash
./config.sh \
  --url https://github.com/ioref/ioref-inventory \
  --token <TIME-LIMITED-TOKEN>
```

It then prompts interactively. Press Enter to accept the default at the first
two prompts:

```text
Enter the name of the runner group to add this runner to: [press Enter for Default]

Enter the name of runner: [press Enter for ioref-web-01]
```

**Do not press Enter at the next one.** This is the prompt that actually
matters:

```text
This runner will have the following labels: 'self-hosted', 'Linux', 'X64'
Enter any additional labels (ex. label-1,label-2): [press Enter to skip] ioref-inventory-production
```

Type `ioref-inventory-production` and confirm you see:

```text
√ Runner successfully added
```

Skipping this prompt registers the runner with only the three default labels.
It costs nothing at registration time, shows no error, and no warning:
`deploy.yml` targets `runs-on: [self-hosted, linux, ioref-inventory-production]`,
so a runner without that label just never picks up a job, and the workflow
sits at "Waiting for a runner to pick up this job..." with nothing to explain
why.

Press Enter for the work folder prompt too (`_work` is fine).

## 8. Configure the runner to use `proxy.andrew.cmu.edu:3128`

Before installing or starting the runner service, create:

```text
/opt/github-actions-runner-inventory/.env
```

with:

```dotenv
http_proxy=http://proxy.andrew.cmu.edu:3128
https_proxy=http://proxy.andrew.cmu.edu:3128
no_proxy=.cmu.edu,.cmu.local,localhost,127.0.0.1
```

The runner reads proxy settings from `.env` in its application directory.

Set ownership:

```bash
chown deploy:deploy /opt/github-actions-runner-inventory/.env
chmod 0644 /opt/github-actions-runner-inventory/.env
```

## 9. Install the runner as a system service

From `/opt/github-actions-runner-inventory`, after the runner has been registered:

```bash
sudo ./svc.sh install deploy
sudo ./svc.sh start
sudo ./svc.sh status
```

The runner service is managed by systemd and runs as `deploy`.

In GitHub, **Settings > Actions > Runners** should now show the runner as
**Idle**.

The runner connects outbound to GitHub through
`proxy.andrew.cmu.edu:3128`. GitHub does not need inbound access to the server
or access to the VPN.

## 10. Verify GitHub connectivity

GitHub's runner configuration script supports a connectivity check. If the
runner cannot connect, use the `--check` command shown in GitHub's runner
troubleshooting documentation and inspect:

```text
/opt/github-actions-runner-inventory/_diag/
```

Also verify the proxy environment:

```bash
sudo -u deploy cat /opt/github-actions-runner-inventory/.env
```

Do not disable TLS certificate verification as a routine workaround.

---

# Repository setup

The manual production deployment workflow lives in the application repository
at `.github/workflows/deploy.yml`, not here, so this runbook cannot drift out
of sync with what actually runs. It triggers on `workflow_dispatch`, targets
the runner by the `ioref-inventory-production` label set at registration
below, and deploys `ghcr.io/ioref/ioref-inventory:sha-<git-sha>` for the
commit selected (or a specific SHA typed into the `sha` input, for a
deliberate rollback).

Use the `production` GitHub Environment to record production deployments and,
if desired, require approval before deployment.

The GHCR package is public, so nothing needs to be granted to `GITHUB_TOKEN`
for the pull to succeed.

---

# First deployment

After the host setup and workflow have been committed:

1. Confirm the desired commit has passed CI.
2. Confirm CI produced the corresponding GHCR image:
   `ghcr.io/ioref/ioref-inventory:sha-<commit-sha>`.
3. Open **Actions** in GitHub.
4. Select **Deploy production**.
5. Select the branch/commit to deploy.
6. Click **Run workflow**.

The self-hosted runner receives the job over its existing outbound GitHub
connection.

It will:

1. pull the exact immutable `sha-*` image (no GHCR login: the package is
   public, and the runner reaches GitHub through a narrow proxy allowlist, so
   every request avoided is one fewer thing that needs allowing there);
2. tag that image locally as `localhost/ioref-inventory:production`;
3. restart the rootless Podman Quadlet;
4. poll the container's own healthcheck for up to a minute, rolling back to
   the previous image if it never turns healthy; and
5. write a summary of what was deployed back to the run.

After the first successful deployment:

```bash
sudo -iu deploy
podman images
```

should show both the pulled GHCR image and the local production tag.

Check the service with:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemctl --user status ioref-inventory-production.service
```

Check application logs with:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
journalctl --user -u ioref-inventory-production.service
```

---

# Deploying updates

Routine application updates should not require SSH access to the production
host.

## 1. Merge the application change

Merge the desired change into the branch used for production.

CI builds the commit and pushes an immutable image such as:

```text
ghcr.io/ioref/ioref-inventory:sha-39a48c8c0cf009d8d9d7a03a54a77087a5e151ea
```

Do not deploy until that build has completed successfully.

## 2. Run the production deployment

In GitHub:

1. Open **Actions**.
2. Select **Deploy production**.
3. Click **Run workflow**.
4. Select the branch containing the commit to deploy.
5. Click **Run workflow**.

The `ioref-inventory-production` runner pulls the immutable image, promotes it to the
local `production` tag, and restarts the application.

## 3. Verify

The workflow itself waits on the container's healthcheck and fails the run
if it never turns healthy, rolling back automatically, so a green run is
already a verified deploy. Confirm from its summary, or on the host:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
podman ps --filter name=ioref-inventory-production
```

should show the container `(healthy)`.

---

# Rollback

The workflow rolls back on its own if the new image never becomes healthy,
restoring whatever was running before it started.

For a deliberate rollback to an older, already-healthy release, re-run
**Deploy production** and fill in the optional `sha` input with the known-good
commit SHA instead of leaving it to default to the selected ref.

If GitHub Actions itself is unreachable, an emergency manual rollback can be
performed on the host as `deploy`:

```bash
export HTTP_PROXY=http://proxy.andrew.cmu.edu:3128
export HTTPS_PROXY=http://proxy.andrew.cmu.edu:3128
export NO_PROXY=.cmu.edu,.cmu.local,localhost,127.0.0.1

podman pull ghcr.io/ioref/ioref-inventory:sha-<KNOWN-GOOD-SHA>
podman tag \
  ghcr.io/ioref/ioref-inventory:sha-<KNOWN-GOOD-SHA> \
  localhost/ioref-inventory:production

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemctl --user restart ioref-inventory-production.service
```

Prefer a workflow-driven rollback over routine manual host access.

---

# Changing production configuration

Production configuration lives outside the container image.

Edit:

```text
/etc/ioref-inventory/production.env
```

as root, then restart the service:

```bash
DEPLOY_UID="$(id -u deploy)"

sudo -u deploy \
  XDG_RUNTIME_DIR="/run/user/${DEPLOY_UID}" \
  systemctl --user restart ioref-inventory-production.service
```

Keep `.env.example` in sync with supported settings. Never commit production
secret values.

---

# Runner maintenance

The GitHub Actions runner normally updates itself automatically.

Check its system service with:

```bash
cd /opt/github-actions-runner-inventory
sudo ./svc.sh status
```

Runner diagnostic logs are in:

```text
/opt/github-actions-runner-inventory/_diag/
```

Check the rootless application service separately:

```bash
DEPLOY_UID="$(id -u deploy)"

sudo -u deploy \
  XDG_RUNTIME_DIR="/run/user/${DEPLOY_UID}" \
  systemctl --user status ioref-inventory-production.service
```

---

# Security notes

The self-hosted runner executes repository workflow commands as `deploy`.
Treat changes to deployment workflows as production access.

- Keep the runner repository-scoped to `ioref/ioref-inventory`.
- Do not use a personal account to run the runner.
- Do not run the runner as root.
- Do not give `deploy` unrestricted passwordless `sudo`.
- Keep production secrets outside the Git checkout.
- Restrict `/etc/ioref-inventory/production.env` to root and the
  `deploy` group.
- Deploy immutable `sha-*` artifacts rather than building source on the
  production server.
- Use a GitHub `production` Environment for production deployments.
- Consider required reviewers on the `production` Environment if production
  deployment should require a second approval.
- Do not make the production runner available to unrelated repositories.
- Do not expose SSH or another inbound service merely for GitHub Actions.

---

# References

- GitHub: Adding self-hosted runners
  https://docs.github.com/actions/hosting-your-own-runners/adding-self-hosted-runners

- GitHub: Configuring a self-hosted runner as a service
  https://docs.github.com/actions/hosting-your-own-runners/managing-self-hosted-runners/configuring-the-self-hosted-runner-application-as-a-service

- GitHub: Using a proxy server with a self-hosted runner
  https://docs.github.com/actions/how-tos/manage-runners/use-proxy-servers

- GitHub: Self-hosted runner reference
  https://docs.github.com/en/actions/reference/runners/self-hosted-runners

- GitHub: Deployment environments
  https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment

- Podman: Quadlet/systemd units
  https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html
