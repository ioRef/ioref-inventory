# ioref-inventory

Django 5.2 + Django REST Framework. Parts inventory for the IDeATe Physical
Computing Lab. In production since August 2026 at `https://ioref.org/inventory`.

`README.md` covers usage and API surface. `DEPLOYMENT.md` is the production
runbook. This document covers architectural rationale, implementation
constraints, and outstanding work.

## System context

ioref.org is two applications sharing one host:

* **ioref-inventory**, this repository, at `/inventory`. Stock.
* **ioref-web**, a separate repository, at `/`. Guide content, served from flat
  markdown under `content/`. Django serving files: `INSTALLED_APPS` is
  `catalog`, `stock` and staticfiles, with no database.

The printed card deck (`ioRef MakerCards 1.26.22.pdf`) is the authoritative list
of what the course explains, and is worth consulting before inventing a
vocabulary. `physcomp-drawio-library` generates drawio shape libraries and is a
candidate for the LTI work.

### Separation of concerns

Stock and guide content are separate domains, and the split runs along that
boundary:

* **Guide content** belongs to **ioref-web**: write-ups, images, part sets, and
  every presentation decision, including which categories to show and what
  color they are.
* **Stock** belongs here: counts, backstock, prices, suppliers, locations,
  minimum and maximum quantities. `Category` also lives here, as the fact that
  a group belongs to Input or Power; see Design decisions.

The two join on `part_number`. The API is keyed on `part_number` rather than on
surrogate primary keys, so ioref-web needs no mapping table.

Guide content must not be added to this repository. The separation exists so
another organization can deploy this application with its own parts catalog.
CMU-specific values belong in configuration, not in code.

## Deployment

Production is `ioref-web-01.andrew.cmu.edu`, RHEL 9. `DEPLOYMENT.md` has the
procedures; what follows is what makes this deployment unusual.

**The app is mounted under a path, not given its own host.** Apache proxies
`/inventory` to gunicorn on `127.0.0.1:8000` **with the prefix intact**, and the
container runs with `SCRIPT_NAME=/inventory`. gunicorn strips the prefix itself
and rejects any request whose path does not start with it, so mapping
`/inventory/` to `/` at the proxy, which is the reflex, produces a 500 on every
request. `FORCE_SCRIPT_NAME` and `STATIC_URL` are both derived from the same
variable in settings, because Django resolves `STATIC_URL` once at startup and a
relative value freezes without the prefix, leaving `{% static %}` pointing at
whatever else is mounted at the root of the host.

**Session and CSRF cookies are renamed** for the same reason. ioref-web will
share the hostname, and two Django applications on one host both default to
`sessionid` and `csrftoken`, which the browser sends together on any request
under the deeper path with neither able to tell which is its own.

**It runs as a rootless Podman Quadlet under the `deploy` account.** The unit is
`~deploy/.config/containers/systemd/ioref-inventory-production.container`. From
any other account:

```bash
systemctl --user --machine=deploy@.host status ioref-inventory-production.service
journalctl --user --machine=deploy@.host -u ioref-inventory-production.service
```

`--machine=deploy@.host` is what reaches another user's systemd instance.
A plain `systemctl --user` from a `su -` session fails with `Failed to connect
to bus: No medium found`, because that shell has no session bus.

**Configuration is `/etc/ioref-inventory/production.env`**, outside the image and
outside the checkout, read through the unit's `EnvironmentFile=`. It is read when
the container is **created**, so `systemctl restart` on the unit applies an edit
and `podman restart` does not: the latter reuses the existing container with the
environment it was built with, which looks exactly like the edit not working.

**Five assertion consumer endpoints are registered with CMU: four on the apex
and one on `inventory.ioref.org`.** The service provider derives its assertion
consumer URL from the `Host` header, so a login completes only on a registered
name. The deployment at `https://ioref.org/inventory` uses the apex endpoint and
needs no change to `shibboleth2.xml`.

`guides.ioref.org` is not registered. Logins there are refused by the IdP with
"Web Login Service - Unable to Respond" and nothing is logged locally, which is
why the vhost redirects that name to the apex rather than serving it. The
inventory registration does not help it; a third endpoint would.

**The Apache vhost is not in this repository.** It is deployment specific and
lives on the host, as `/etc/httpd/conf.d/02-ioref.org.conf`, where it serves the
ioref-web at `/` and this application at `/inventory`. What it has to guarantee
is recorded in the Apache section of `DEPLOYMENT.md`, because the vhost is the
only thing standing between the header-trust backend and anyone who can reach
it, and that is too important to exist only on one host.

## Design decisions

**Append-only history.** A count is an observation, not a value to overwrite.
`StockEvent` and `PriceObservation` are only ever inserted, and current values
are derived from them, so two people counting at once each add a row instead of
racing to replace one, and nothing stores a total that can drift from the events
under it. A correction is a newer observation, so what was believed and when
survives it.

**`Group` is separate from `Location`, and holds only the fine level.** Two
fields, so a part can be reclassified without appearing to move and moved
without appearing to be reclassified.

`Potentiometers`, `Capacitors` and `Diodes` would mean something to any
organization; `Input` only means something to a course, which is why the macro
level is `Category` on the group rather than part of the group's name.

**`Category` is here, its appearance is not.** Filing a group under Input or
Power is a teaching decision, and the person making it is the person creating
the group, so asking them to edit a second repository to record it is how a
taxonomy goes stale. It is rows rather than code: a deployment with no guides
site leaves the table empty and every group keeps `category = None`, which is
also the right state for `Fasteners` here. Categories carry no color and no
ordering, because which ones to display and how is ioref-web's call and two
answers to that question is one too many.

**The group vocabulary was derived once and is now edited by hand.** It came out
of the migration by taking the head noun of each part's own name, then curating
the result: merging topics split across two words, dropping heads that named no
topic, and stating outright the groups a maker card already explains. Nothing
derives them now. A new part gets a group chosen in the admin.

Two things about the shape it left behind, because both look arbitrary:

* `Fasteners` is one group over about 190 parts while `Photoresistors` holds
  one. Granularity follows teaching value, not part count. The guides are about
  electronics, so screws, bolts, nuts and washers are one page nobody will
  write, while the single photoresistor is a page someone already did.
* Sensors stay split by what they sense, under a shared `sensor` tag. What a
  pressure sensor is for has nothing to say about what a light sensor is for, so
  one page covering both would explain neither. `?tag=sensor` answers "what can
  I sense", which the split would otherwise lose.

Groups that share a word rather than a topic are the recurring defect here.
`Couplers` and `Couplings` looked like one topic spelled two ways and were three
topics spread across both: tubing, shaft and audio. Expect more of these, and fix
them when someone notices one.

**A use is a tag, not a group.** `Tag` carries `soldering`, `tool box`,
`lending` and `touch`, taken from the bins, alongside the functional labels from
the card deck: `movement`, `light`, `sound`, `position`, `proximity`,
`temperature`, `sensor` and the rest. A soldering bench holds an iron tip, a flux
pen, a sponge and a pair of helping hands, which share no head noun and no rule
over part names would ever collect. `?group=` answers "what kind of thing",
`?tag=` answers "what is this for".

**`Group` is singular, `Tag` is plural, and the distinction is load-bearing.** A
part is one kind of thing, so `group` is a foreign key, which is what makes
"every capacitor below minimum" and "which component page covers this part"
answerable without ambiguity. Two type-ish tags would break both.

**`Location` is a model, not a character field.** The previous CSV contained
several hundred "Empty" placeholder rows that its loader discarded for lacking a
part number, losing the record that the bin exists. Empty bins are rows here.

**`None` is distinct from `0`.** An uncounted part reports `None`. A sentinel
that arithmetic can reach turns "we have never looked" into a stock level.

**Authentication is pluggable.** `AUTH_MODE` accepts `local`, `shib` or `oidc`.
No SAML or OIDC library is imported at module scope, so migrating from
Shibboleth to Entra is a configuration and proxy change rather than an
application change.

**Usernames are eppns, not bare Andrew IDs.** CMU releases
`user@andrew.cmu.edu`. A bare username is unique only within one institution,
where an eppn is unique across the federation and maps onto Entra's UPN when
that migration happens. Django's default username validator already permits `@`.
Changing this after accounts exist produces a duplicate for every user, since
`TrustedHeaderBackend` resolves on it.

**Signing in does not create an account.** Apache admits anyone holding a CMU
session, because the roster lives in Django as `is_staff` rather than in
`Require shib-user` lines. `TrustedHeaderBackend` resolves accounts and never
creates them, so an eppn with no account authenticates as nobody and the request
continues anonymously. `manage.py grant_staff` is how an account comes to exist.

That asymmetry is deliberate and load-bearing. `_may_see_costs` in
`inventory/views.py` turns on `is_authenticated` rather than `is_staff`, so
provisioning a stranger would hand them the prices and suppliers the public
views deliberately withhold.

**A custom user model was defined at project start.** Django cannot swap
`AUTH_USER_MODEL` once production data exists without a manual migration, so
`accounts.User` exists from the beginning. `subject_id` holds the IdP's permanent
identifier where one is released and takes precedence over eppn when resolving an
account, so a rename follows the person and a reissued eppn does not inherit the
previous holder's history. **CMU releases no such identifier**: the attributes
are eppn, mail, displayName, cn, givenName, sn, affiliation and entitlement, and
none of subject-id, pairwise-id or persistent-id. The field is wired and inert
here, and the rename hazard is unmitigated by anything in this codebase.

## Implementation constraints

**Annotations must not be named `on_floor` or `in_backstock`.** Both are
read-only properties on `Part`. Properties are data descriptors, which
`setattr()` cannot write through, and `setattr()` is how Django applies
annotations. The `_ann_` prefix in `api/views.py` and `inventory/admin.py` exists
for this reason; `Part._latest_quantity` reads the annotations back. Renaming
them raises `AttributeError`; removing them reintroduces an N+1 query per row.

**`AUTH_MODE=shib` trusts its request headers unconditionally.**
`accounts/backends.py` accepts whatever `REMOTE_USER_HEADER` contains. That is
safe only where the upstream proxy overwrites those headers on every request, and
only where the application is unreachable except through it. The container port
is bound to `127.0.0.1` for the second half of that.

The `RequestHeader unset ... early` directives in the vhost are the first half,
and `early` is not decoration: without it mod_headers runs at the fixup hook,
*after* mod_shib has populated the headers from the SAML session, so the unset
strips the authentic values and breaks login rather than blocking the forgery.
The block must name every header the application reads, so adding a
`REMOTE_USER_*_HEADER` setting without adding it there reopens the bypass. See
the Apache section of `DEPLOYMENT.md`.

**The API must remain outside Shibboleth.** ioref-web is a service and cannot
complete a browser redirect to an identity provider. `/inventory/admin` uses
`requireSession 1`; `/inventory/api` uses `AuthType None` with bearer-key
authentication enforced by DRF. Applying `requireSession 1` to the whole vhost
breaks ioref-web.

**The public views must stay unbranded.** `inventory/templates/inventory/` and
`public.css` carry no house styling, because this application is meant to be
deployable by other organizations. CMU presentation belongs in ioref-web, which
renders the same data over the API in IDeATe's colors.

**Anonymous visitors must not see prices or suppliers.** `inventory/views.py`
skips querying them entirely rather than fetching and hiding them in the
template, so a template change cannot leak them. Covered in `inventory/tests.py`.

**The static storage backend is swapped during tests.** The manifest storage
needs a `collectstatic` run to resolve `{% static %}`, which is right for a
deployment and wrong for a test suite; `settings.TESTING` handles it.

**Unfold overrides require `!important`.** Unfold applies sizing and color
through Tailwind utility classes on the elements themselves, which ordinary
selectors cannot override regardless of specificity. This accounts for the
`!important` declarations in `inventory/static/inventory/theme.css`.

**Unfold owns the admin templates.** Django upgrades may break the admin in ways
stock Django would not. The version is pinned in `uv.lock`; upgrade deliberately.

## Design language

IDeATe's house palette, shared with the guides site.

* Typeface Nunito Sans. Information pages use 24px bold titles, 22px bold section
  headings, bold labels and 16px body text. The admin matches this scale, raised
  from Unfold's default 12 to 14px for accessibility.
* Neutral grays with no blue component: `#fdfdfd`, `#f2f2f2`, `#c4c4c4`,
  `#636466`, `#4f4f4f`, `#1d1d1d`. Input green (`#14B04D`) is the admin
  primary. The other category colors are not defined here; see `Category` in
  Design decisions.
* The stock column marks exceptions only: crimson below minimum, gray never
  counted, plain numeral otherwise. Healthy rows are left undecorated so rows
  needing attention stay visible.

`primary-600` is set darker than a linear ramp would place it (`#0c8138`).
Unfold uses shade 600 for filled buttons with white labels, and the brand green
manages only about 2.4:1 against white.

## Commits

Conventional Commits, e.g. `feat(api): add part_number__in filter`.

Types in use: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`,
`build`, `ci`. Scope is the app or area (`api`, `inventory`, `accounts`,
`catalog`, `stock`, `deploy`, `tooling`). Breaking changes take a `!` before the
colon and a `BREAKING CHANGE:` footer.

The subject is imperative and lower-case, under about 72 characters. Bodies say
what the diff cannot: the reasoning behind a decision, not a restatement of the
change. This project has several constraints that look arbitrary without it.

## Commands

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py seed_demo --flush
uv run python manage.py grant_staff you@andrew.cmu.edu --superuser
uv run python manage.py runserver
uv run python manage.py test
docker compose up --build
prek run --all-files
```

`seed_demo` loads a subset of real records and deliberately includes a
below-minimum part (`0010`), an uncounted part (`0054`) and a discontinued part
at zero (`0028`). Changes to the stock display should be verified against these.

`grant_staff` is the only way an account comes to exist under `AUTH_MODE=shib`,
since there is no local login form. Usernames are eppns; a bare Andrew ID is
refused because the SSO headers would never match it. `--revoke` withdraws access
without deleting the row, because stock events reference who recorded them.

## Build and release

`.github/workflows/build.yml` runs the tests, then publishes an image to
`ghcr.io/ioref/ioref-inventory` tagged `sha-<git-sha>`. A self-hosted runner on
the production host pulls that tag through `proxy.andrew.cmu.edu:3128`, retags it
`localhost/ioref-inventory:production` and restarts the Quadlet. The mutable tag
exists only on the host, so the registry stays a record of what was built rather
than a moving target, and rollback is pulling an older sha.

`.github/workflows/deploy.yml` is the manual trigger that does the pull, retag
and restart, on that same runner. It checks out nothing and does not log in to
GHCR: the image is public, so `podman pull` needs no credential, and the fewer
requests the runner makes through the proxy the fewer things need allowing
there.

The runner executes workflow commands as `deploy`. **Editing a deployment
workflow is production access**, and should be reviewed as such.

Pull requests build the image but do not push it, so a broken Dockerfile is
caught before merge without an unreviewed tag reaching the registry.

**The test job needs no database service and no `.env`.** Every setting in
`config/settings.py` has a default, `DATABASE_URL` falls back to SQLite, and
Django builds its own test database in memory. Adding a Postgres service would be
right only if prod moves to Postgres, at which point CI should matrix over both.
`psycopg2-binary` is already a dependency and `compose.yaml` already has the
profile, but nothing currently tests that path.

**`provenance` and `sbom` are disabled in the build step, for podman's benefit.**
buildx attaches attestation manifests by default, which turns a single-platform
build into a manifest list whose extra entries carry no architecture. Podman
rejects that with "no image found in manifest list for architecture amd64".
Re-enable only after confirming prod's podman skips unknown entries.

The image name is written out in lowercase rather than taken from
`github.repository`, because the organization is spelled `ioRef` and registry
paths must be lowercase.

`.dockerignore` earns its place: the Dockerfile ends with `COPY . .`, so without
it a build bakes the developer's `data/db.sqlite3`, or a `production.env` holding
`SECRET_KEY`, into a published image.

## Maintenance

Everything here is pinned on purpose, which means nothing moves unless something
moves it. `.github/dependabot.yml` is that something: monthly pull requests for
the Dockerfile's two `FROM` lines, the workflow's actions and `uv.lock`. Patches
are grouped; anything larger arrives on its own so it has somewhere to argue for
itself.

**uv.** Pinned at `0.12.5` in the Dockerfile. The image's uv must be at least as
new as whatever wrote `uv.lock`, currently `revision = 3`. A newer uv reads an
older lockfile but not the reverse, so a developer on a newer uv than the image
is the failure case.

**Base image.** `python:3.13-slim` moves for security fixes without changing the
tag, so a periodic rebuild matters as much as the version bump does.

**Django.** 5.2 is an LTS, supported to April 2028. Dependabot is configured not
to offer the major, because moving off an LTS is a migration rather than a
dependency bump. Security releases within 5.2 should be taken promptly.

**Unfold.** It owns the admin templates, so it is the upgrade most likely to
break something visible, and the breakage lands in the admin rather than in a
test. Check the changelog against `inventory/static/inventory/theme.css`. Upgrade
it deliberately, and not in the same pull request as a Django upgrade.

Four things Dependabot cannot see:

**The TLS certificate.** One InCommon SAN certificate covers `ioref.org`,
`guides`, `inventory`, `admin`, `ioref-web-01.andrew.cmu.edu` and
`ioref.ideate.cmu.edu`. It expires **2026-12-09** and is renewed through
InCommon, not certbot, despite living under an `/etc/letsencrypt` path.

**The Shibboleth service provider keypair.** It must survive container rebuilds,
or the metadata registered with the identity provider goes stale and logins fail.
Certificates expire; the renewal is a coordination with the IdP, not a deploy.

**API keys.** `ApiKey.expires_at` is nullable, so a key issued without one never
expires. Worth reviewing periodically against who still needs a key, and rotating
the ioref-web key on the same occasion.

**Group hygiene.** Nothing derives groups any more, so nothing prunes them
either. A group emptied by reassignment stays as an empty heading.

## Outstanding work

**Count-entry interface.** The admin is functional but does not match the
operational workflow, which is barcode-driven: scan, enter a quantity, advance.
That wants a single field without pointer input. Unfold's keyboard shortcuts
(`c` to create, `Ctrl+K` for the command palette, `Shift+?` for the full list)
assist navigation but not data entry. Access control should be enforced in Django
with `@login_required` in addition to Apache, so it does not depend on deployment
configuration.

**Moving to `inventory.ioref.org`.**
`https://inventory.ioref.org/Shibboleth.sso/SAML2/POST` is now registered, so
this application can move to its own hostname instead of a path on the shared
one whenever that is wanted. Doing so drops `SCRIPT_NAME`, `FORCE_SCRIPT_NAME`,
the derived `STATIC_URL` prefix and the renamed cookies, and changes every
published `/inventory/...` URL, so it is a planned migration rather than a
configuration change. `DEPLOYMENT.md` covers what moves.

That registration does **not** fix `guides.ioref.org`, which is still
unregistered and still redirected to the apex.
