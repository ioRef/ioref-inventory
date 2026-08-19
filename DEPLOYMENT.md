# Production Deployment

ioRef Inventory production runs on **Red Hat Enterprise Linux 9**.

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
Description=ioRef Inventory Production
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

## 6. Install the GitHub Actions runner

Register the runner directly to **ioRef/ioref-inventory**.

First create the runner directory:

```bash
mkdir -p /opt/github-actions-runner
chown deploy:deploy /opt/github-actions-runner
```

Then switch to the `deploy` account:

```bash
sudo -iu deploy
```

Once the `deploy` shell opens, change into the runner directory:

```bash
cd /opt/github-actions-runner
```

Set the proxy variables for the bootstrap commands:

```bash
export HTTP_PROXY=http://proxy.andrew.cmu.edu:3128
export HTTPS_PROXY=http://proxy.andrew.cmu.edu:3128
export NO_PROXY=.cmu.edu,.cmu.local,localhost,127.0.0.1
```

These exports are only for downloading and configuring the runner. The runner's
persistent proxy configuration is set in `/opt/github-actions-runner/.env`
below.

In GitHub:

1. Open **ioRef/ioref-inventory**.
2. Open **Settings**.
3. Open **Actions > Runners**.
4. Select **New self-hosted runner**.
5. Choose Linux and the architecture of the production server.

GitHub provides the current download commands and a time-limited registration
token. Use the download, extraction, and configuration commands shown there.

> GitHub's instructions normally begin by creating an `actions-runner`
> directory and changing into it. Skip those `mkdir` and `cd` commands:
> `/opt/github-actions-runner` already exists and the `deploy` shell should
> already be in that directory.

When running the generated `config.sh` command, add the production-specific
label:

```text
ioref-inventory-production
```

For example, the generated command will have this general form:

```bash
./config.sh \
  --url https://github.com/ioRef/ioref-inventory \
  --token <TIME-LIMITED-TOKEN> \
  --name "$(hostname -s)" \
  --labels ioref-inventory-production
```

Use the registration token generated by GitHub.

## 7. Configure the runner to use `proxy.andrew.cmu.edu:3128`

Before installing or starting the runner service, create:

```text
/opt/github-actions-runner/.env
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
chown deploy:deploy /opt/github-actions-runner/.env
chmod 0644 /opt/github-actions-runner/.env
```

## 8. Install the runner as a system service

From `/opt/github-actions-runner`, after the runner has been registered:

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

## 9. Verify GitHub connectivity

GitHub's runner configuration script supports a connectivity check. If the
runner cannot connect, use the `--check` command shown in GitHub's runner
troubleshooting documentation and inspect:

```text
/opt/github-actions-runner/_diag/
```

Also verify the proxy environment:

```bash
sudo -u deploy cat /opt/github-actions-runner/.env
```

Do not disable TLS certificate verification as a routine workaround.

---

# Repository setup

Add the manual production deployment workflow at:

```text
.github/workflows/deploy-production.yml
```

The workflow deploys the image already built and pushed by CI:

```text
ghcr.io/ioref/ioref-inventory:sha-<git-sha>
```

for the commit being deployed.

A suitable workflow is:

```yaml
---
name: Deploy production

on:
  workflow_dispatch:

permissions:
  contents: read
  packages: read

jobs:
  deploy:
    name: Deploy production
    runs-on:
      - self-hosted
      - linux
      - ioref-inventory-production

    environment: production

    steps:
      - name: Select image
        id: image
        shell: bash
        run: |
          echo "source=ghcr.io/ioref/ioref-inventory:sha-${GITHUB_SHA}" \
            >> "$GITHUB_OUTPUT"

      - name: Log in to GHCR
        shell: bash
        run: |
          printf '%s' "${{ secrets.GITHUB_TOKEN }}" |
            podman login ghcr.io \
              --username "${{ github.actor }}" \
              --password-stdin

      - name: Pull deployment image
        shell: bash
        run: |
          podman pull "${{ steps.image.outputs.source }}"

      - name: Promote image locally
        shell: bash
        run: |
          podman tag \
            "${{ steps.image.outputs.source }}" \
            localhost/ioref-inventory:production

      - name: Restart application
        shell: bash
        run: |
          export XDG_RUNTIME_DIR="/run/user/$(id -u)"
          systemctl --user daemon-reload
          systemctl --user restart ioref-inventory-production.service

      - name: Verify application
        shell: bash
        run: |
          export XDG_RUNTIME_DIR="/run/user/$(id -u)"
          systemctl --user --no-pager status ioref-inventory-production.service
          podman ps --filter name=ioref-inventory-production
```

`workflow_dispatch` adds the **Run workflow** button in GitHub Actions.

Use the `production` GitHub Environment to record production deployments and,
if desired, require approval before deployment.

If the GHCR package is private, grant the repository's `GITHUB_TOKEN` read
access to it.

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

1. authenticate to GHCR;
2. pull the exact immutable `sha-*` image;
3. tag that image locally as `localhost/ioref-inventory:production`;
4. restart the rootless Podman Quadlet; and
5. report the service status back to GitHub.

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

The deployment should finish with:

- `ioref-inventory-production.service` active;
- the `ioref-inventory` container running; and
- the image pull and restart succeeding.

If the application exposes a health endpoint, verify it here as well.

---

# Rollback

Rollback deploys an older known-good immutable `sha-*` image.

A `sha` input on `workflow_dispatch` can make this selectable directly from
the GitHub UI.

Until then, an emergency manual rollback can be performed on the host as
`deploy`:

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
cd /opt/github-actions-runner
sudo ./svc.sh status
```

Runner diagnostic logs are in:

```text
/opt/github-actions-runner/_diag/
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

- Keep the runner repository-scoped to `ioRef/ioref-inventory`.
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
