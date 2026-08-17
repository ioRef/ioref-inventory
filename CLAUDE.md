# ioref-inventory

Django 5.2 + Django REST Framework. Parts inventory for the IDeATe Physical
Computing Lab. Initial implementation August 2026, replacing part of the
existing ioref.org stack.

`README.md` covers usage and API surface. This document covers architectural
rationale, implementation constraints, and outstanding work.

## System context

ioref.org currently runs as three applications and is being rebuilt as two.

| Current | Replacement | Status |
|---|---|---|
| **Directus** (`admin.ioref.org`, MySQL `phys_comp_prod`), the CMS and identity provider | retired | in production |
| **maker-cards** (`guides.ioref.org`, Express + Handlebars) | **ioref-web** (Wagtail, separate repository) | scaffolded |
| **IDeATe-Inventory** (Express, port 3000) | **ioref-inventory** (this repository) | scaffolded |

The applications being replaced are the authoritative reference for current
behaviour. Check them out alongside this repository:

- **IDeATe-Inventory**: existing inventory application. Its `data.csv` is out
  of date; `upload.py` is a reliable reference for field semantics only.
- **maker-cards**: existing guides site. `public/css/main.scss` is the source
  of truth for visual design; `conf/apache/` documents the deployment pattern.
- **cms**: the Directus installation.
- **physcomp-drawio-library**: Python; generates drawio shape libraries.
  A candidate for the LTI work.
- `ioref-schema.yaml`: Directus schema snapshot; the migration reference.

### Separation of concerns

The Directus `parts` collection is a 42-column table combining two unrelated
domains. The rebuild divides it along that boundary:

- **Guide content** belongs to **ioref-web**: `docs_about`, `docs_what_it_is`,
  `docs_how_it_works` and four further `docs_*` fields, plus images,
  categories, subcategories, part-sets and related-parts.
- **Stock** belongs to **this repository**: counts, backstock, prices,
  suppliers, locations, minimum and maximum quantities.

The two join on `part_number`. The API is keyed on `part_number` rather than on
surrogate primary keys so that the frontdoor requires no mapping table.

Guide content must not be added to this repository. The separation exists so
that another organisation can deploy this application with its own parts
catalogue. CMU-specific values belong in configuration, not in code.

## Design decisions

**Append-only history.** The previous schema stored five JSON objects keyed by
timestamp (`inventory_history`, `price_history`, and others) alongside
denormalised `current_*` columns. Each update read the entire object, modified
it, and wrote it back (`IDeATe-Inventory/app.js:171-206`), so concurrent counts
silently overwrote one another, and the denormalised columns could diverge from
the history they summarised. `StockEvent` and `PriceObservation` are
append-only; current values are derived. Corrections are recorded as new
observations rather than edits, preserving the audit trail.

**`Group` is separate from `Location`, and holds only the fine level.** The
legacy schema had no classification field, so it was smuggled into the place
field: 464 of 1,467 rows in `data.csv` carry a location shaped
`Input: Potentiometers`. That meant a part could not be reclassified without
appearing to move, nor moved without appearing to be reclassified.

Only the fine half is inventory's business. The macro half is a
physical-computing teaching taxonomy, and the two do not even agree: its largest
value, `Electrical Components` (154 parts), is not one of ioref-web's five
categories, and ioref-web's `Power` has no parts under it at all. Which macro
category a group sits under is the frontdoor's call. `Potentiometers`,
`Capacitors`, `Diodes` would mean something to any organisation; `Input` only
means something to a physical computing course.

**`Group` is singular, `Tag` is plural, and the distinction is load-bearing.**
A part is one kind of thing, so `group` is a foreign key: that is what makes
"every capacitor below minimum" and "which component page covers this part"
answerable without ambiguity. Two type-ish tags would break both. `Tag` carries
cross-cutting facts that are not the type -- part 0386 is a soft linear
potentiometer whose group is `Potentiometers` and whose tag is `touch`, because
the legacy data filed it under "Touch" and lost the fact that it is a pot.

**`Location` is a model, not a character field.** The previous CSV contained
several hundred "Empty" placeholder rows that `upload.py` discarded for lacking
a part number, losing the record that the bin exists. Empty bins are rows here.

**`None` is distinct from `0`.** The previous implementation used `-1` as a
"never counted" sentinel and performed arithmetic on it. An uncounted part
reports `None`.

**Authentication is pluggable.** `AUTH_MODE` accepts `local`, `shib`, or
`oidc`. No SAML or OIDC library is imported at module scope, so migrating from
Shibboleth to Entra is a configuration and proxy change rather than an
application change.

**Usernames are eppns, not bare Andrew IDs.** CMU releases
`user@andrew.cmu.edu`. A bare username is unique only within one institution,
whereas eppn is unique across the federation (which the deploy-elsewhere goal
requires) and maps directly onto Entra's UPN when that migration happens.
Django's default username validator already permits `@`, so no field override
is needed. Note that changing this after accounts exist produces a duplicate
for every user, since `TrustedHeaderBackend` resolves on it.

**A custom user model was defined at project start.** Django cannot swap
`AUTH_USER_MODEL` once production data exists without a manual migration, so
`accounts.User` exists from the beginning even though it adds only two fields.
`subject_id` holds the IdP's permanent identifier (eduPersonUniqueId or the
SAML persistent NameID) where one is released; it takes precedence over eppn
when resolving an account, so a rename follows the person and a reissued eppn
does not inherit the previous holder's history. It is optional: not every
deployment's IdP releases one, and resolution falls back to eppn alone.
`accounts/tests.py` covers both paths.

## Implementation constraints

**Annotations must not be named `on_floor` or `in_backstock`.** Both are
read-only properties on `Part`. Properties are data descriptors, which
`setattr()` cannot write through, and `setattr()` is the mechanism Django uses
to apply annotations. The `_ann_` prefix in `api/views.py` and
`inventory/admin.py` exists for this reason; `Part._latest_quantity` reads the
annotations back. Renaming them raises `AttributeError`; removing them
reintroduces an N+1 query per row.

**`AUTH_MODE=shib` trusts its request headers unconditionally.**
`accounts/backends.py` accepts whatever `REMOTE_USER_HEADER` contains. This is
safe only where the upstream proxy overwrites those headers on every request;
see the `RequestHeader unset` directives in `deploy/apache/inventory.conf`. If
gunicorn is reachable other than through that virtual host, a client-supplied
`Remote-User` header constitutes a complete authentication bypass. Under Docker,
the application container's port must not be published; only Apache is exposed.

**The API must remain outside Shibboleth.** ioref-web is a service and
cannot complete a browser redirect to an identity provider. `/admin` uses
`requireSession 1`; `/api` uses `requireSession 0` with bearer-key
authentication enforced by DRF. Applying `requireSession 1` to the whole virtual
host will break the frontdoor.

**The public views must stay unbranded.** `inventory/templates/inventory/` and
`public.css` carry no house styling, because this application is meant to be
deployable by other organisations. CMU presentation belongs in ioref-web, which
renders the same data over the API in IDeATe's colours.

**Anonymous visitors must not see prices or suppliers.** `inventory/views.py`
skips querying them entirely rather than fetching and hiding them in the
template, so a template change cannot leak them. Covered in
`inventory/tests.py`.

**The static storage backend is swapped during tests.** The manifest storage
needs a `collectstatic` run to resolve `{% static %}`, which is right for a
deployment and wrong for a test suite; `settings.TESTING` handles it.

**Unfold overrides require `!important`.** Unfold applies sizing and colour
through Tailwind utility classes on the elements themselves, which ordinary
selectors cannot override regardless of specificity. This accounts for the
`!important` declarations in `inventory/static/inventory/theme.css`.

**Unfold owns the admin templates.** Django upgrades may break the admin in ways
that stock Django would not. The version is pinned in `uv.lock`; upgrade
deliberately.

## Design language

Derived from `maker-cards/public/css/main.scss`, which is authoritative.

- Typeface Nunito Sans. Information pages use 24px bold titles, 22px bold
  section headings, bold labels, and 16px body text. The admin matches this
  scale, raised from Unfold's default 12–14px for accessibility.
- Neutral greys with no blue component: `#fdfdfd`, `#f2f2f2`, `#c4c4c4`,
  `#636466`, `#4f4f4f`, `#1d1d1d`.
- Category colours: input `#14B04D`, output `#00A0C4`, controller `#4C265B`,
  connector `#636466`, power `#DD1B50`. Input green is the admin primary.
- The stock column marks exceptions only: crimson below minimum, grey never
  counted, plain numeral otherwise. Healthy rows are left undecorated so that
  rows requiring attention remain visible.

`primary-600` is set darker than a linear ramp would place it (`#0c8138`).
Unfold uses shade 600 for filled buttons with white labels, and the brand green
achieves only about 2.4:1 contrast against white.

## Commits

Conventional Commits, e.g. `feat(api): add part_number__in filter`.

Types in use: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`,
`build`, `ci`. Scope is the app or area (`api`, `inventory`, `accounts`,
`catalog`, `stock`, `deploy`). Breaking changes take a `!` before the colon and
a `BREAKING CHANGE:` footer.

The subject is imperative and lower-case, under about 72 characters. Where a
change encodes a decision, put the reasoning in the body -- this project has
several constraints that look arbitrary without it.

## Commands

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py seed_demo --flush
uv run python manage.py createsuperuser
uv run python manage.py runserver
uv run python manage.py test
docker compose up --build
```

`seed_demo` loads a subset of real legacy records and deliberately includes a
below-minimum part (`0010`), an uncounted part (`0054`), and a discontinued part
at zero (`0028`). Changes to the stock display should be verified against these.

## Build and release

`.github/workflows/build.yml` runs the tests, then publishes an image to
`ghcr.io/ioref/ioref-inventory` for podman to pull on prod. Pull requests build
the image but do not push it, so a broken Dockerfile is caught before merge
without an unreviewed tag reaching the registry.

**The test job needs no database service and no `.env`.** Every setting in
`config/settings.py` has a default, `DATABASE_URL` falls back to SQLite, and
Django builds its own test database in memory. Adding a Postgres service would
be the right move only if prod moves to Postgres, at which point CI should
matrix over both. `psycopg2-binary` is already a dependency and
`compose.yaml` already has the profile, but nothing currently tests that path.

**`provenance` and `sbom` are disabled in the build step, for podman's
benefit.** buildx attaches attestation manifests by default, which turns a
single-platform build into a manifest list whose extra entries carry no
architecture. Podman rejects that with "no image found in manifest list for
architecture amd64". Re-enable only after confirming prod's podman skips
unknown entries.

The image name is written out in lowercase rather than taken from
`github.repository`, because the organisation is spelled `ioRef` and registry
paths must be lowercase.

`.dockerignore` earns its place: the Dockerfile ends with `COPY . .`, so
without it a local `docker compose up --build` bakes the developer's
`data/db.sqlite3` into the image, where the volume mount then shadows it. Real
stock data in a registry, to no purpose.

## Maintenance

Everything here is pinned on purpose, which means nothing moves unless
something moves it. `.github/dependabot.yml` is that something: monthly pull
requests for the Dockerfile's two `FROM` lines, the workflow's actions, and
`uv.lock`. Patches are grouped; anything larger arrives on its own so it has
somewhere to argue for itself.

What to check when one lands:

**uv.** Pinned at `0.12.5` in the Dockerfile. The image's uv must be at least
as new as whatever wrote `uv.lock`, currently `revision = 3`. A newer uv reads
an older lockfile, but not the reverse, so a developer on a newer uv than the
image is the failure case. If a bump breaks the build, compare against
`uv --version` locally before assuming upstream is at fault.

**Base image.** `python:3.13-slim` moves for security fixes without changing
the tag, so a periodic rebuild matters as much as the version bump does. A
scheduled build, or just merging the monthly Dependabot pull request, refreshes
it.

**Django.** 5.2 is an LTS, supported to April 2028. Dependabot is configured
not to offer the major, because moving off an LTS is a migration rather than a
dependency bump. Security releases within 5.2 should be taken promptly.

**Unfold.** It owns the admin templates, so it is the upgrade most likely to
break something visible, and the breakage lands in the admin rather than in a
test. Check the changelog against `inventory/static/inventory/theme.css`, whose
`!important` declarations exist to override Unfold's Tailwind utility classes.
Upgrade it deliberately, and not in the same pull request as a Django upgrade.

Two things Dependabot cannot see:

**The Shibboleth service provider keypair.** It must survive container
rebuilds, or the metadata registered with the identity provider goes stale and
logins fail. Certificates expire; the renewal is a coordination with the IdP,
not a deploy. See the Docker note under "Outstanding work".

**API keys.** `ApiKey.expires_at` is nullable, so a key issued without one
never expires. Worth reviewing periodically against who still needs a key, and
the ioref-web key rotated on the same occasion.

## Outstanding work

**Directus migration.** Not yet written. Before beginning, determine which
source is authoritative: the schema contains both an `Inventory` collection
(append-only: `part`, `item_count`, `date_created`, indicating that migration
away from the JSON objects was already underway) and the legacy
`inventory_history` field on `parts`. This requires checking against production
data. The migration will also need `directus_files` metadata and the uploads
directory for images, which belong to the frontdoor rather than this repository.
`directus-dump.sh` exports a complete instance over HTTP and should be moved
into `tools/`.

**Component grouping.** ioref-web groups several stocked parts under one
component page, so one explanation covers all 33 ceramic capacitors. The
*explanation* lives there. The *classification* lives here, as `Category`, so
the frontdoor can propose groupings from `?category=`, and purchasing questions
like "every capacitor below minimum" can be answered without involving the
guides site at all.

**Count-entry interface.** The Django admin is functional but does not match the
operational workflow, which is barcode-driven: scan, enter a quantity, advance.
That wants a single field, without pointer input. Unfold's keyboard shortcuts (`c` to
create, `Ctrl+K` for the command palette, `Shift+?` for the full list) assist
navigation but not data entry. Access control should be enforced in Django with
`@login_required` in addition to Apache, so that it is not dependent on
deployment configuration.

**`parts.part_set` should be many-to-many.** It is a single foreign key in
Directus, restricting a part to one set. This is incorrect for project component
sets, where a given part appears in many. To be addressed in the frontdoor.

**LTI integration.** Under consideration for the drawio library or part-sets,
launched from Canvas. This targets the frontdoor; inventory need only serve the
API. LTI 1.3 is OIDC-based and launches within an iframe, requiring
`SameSite=None` cookies, a pattern maker-cards already established for its
Directus session cookie.

**Shibboleth under Docker.** `mod_shib` is an Apache module requiring `shibd`
alongside it and cannot run in the Python image. This requires a second service
in `compose.yaml` with a persistent volume, as the `shibd` service provider
keypair must survive container rebuilds or the metadata registered with the
identity provider becomes stale. The service provider is already registered.

## Security note

`IDeATe-Inventory/app.js:7` contains a live Directus access token committed to
version control. It should be rotated at cutover; that repository may be
published to GitHub.
