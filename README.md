# ioref-inventory

Parts inventory: stock levels, locations, prices, suppliers, and their history.

Deliberately knows nothing about guides, categories, maker cards, or images.
Those live in **ioref-web**, a separate repository that joins to this one
on `part_number` over the API below. That split is what lets this app be
deployed by an organization that has its own parts and no interest in ours.

## Quick start

```bash
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Admin at `/admin/`, API at `/api/v1/`.

```bash
docker compose up --build     # or containerised
```

## Data model

Six tables:

| Model | Purpose |
|---|---|
| `Part` | Identity and stocking policy: number, name, unit, min/max, status |
| `Group` | What kind of part it is. One per part |
| `Tag` | Cross-cutting facts that are not the type. Many per part |
| `Location` | Where it lives. First-class, so an empty bin is a row |
| `StockEvent` | Append-only count: `(part, kind, quantity, observed_at)` |
| `PriceObservation` | Append-only price + supplier + purchase link |

**Why append-only.** A count is an observation, not a value to overwrite. Two
people counting at once each add a row rather than racing to replace one, and a
correction is a newer count, so what was believed and when survives.

Current values are derived (`Part.on_floor`, `.total_on_hand`, `.needs_restock`)
and annotated in list queries, so a page costs a constant number of queries.
Nothing stores a running total that could drift from the events under it.

A price, its supplier and its purchase link are one row, so a price is always
attributable to who sold it at that price.

A part never counted reports `None`. That is not zero, and the difference
matters when deciding what to reorder.

## Browsing

`/` serves read-only HTML: a searchable, filterable parts list and a per-part
page with count history. It exists so the application is useful on its own: a
deployment with no site in front of it still needs a way to look at its own stock.

Deliberately unbranded. House styling belongs in whatever site sits in front;
ioref-web renders the same data in IDeATe's colors.

**Prices and suppliers are shown only to signed-in users.** Stock levels answer
"do you have any, and where"; what it cost and who sold it is procurement's
business. The view does not query them at all for anonymous visitors.

Set `PUBLIC_BROWSE=False` to require a login for the HTML views entirely. The
API is unaffected either way.

## API

`/api/v1/`, keyed on `part_number`.

| Method | Path | Scope |
|---|---|---|
| `GET` | `/parts/` | read |
| `GET` | `/parts/{part_number}/` | read |
| `GET` | `/parts/{part_number}/stock-events/` | read |
| `POST` | `/parts/{part_number}/stock-events/` | write |
| `GET` | `/parts/{part_number}/prices/` | read |
| `POST` | `/parts/{part_number}/prices/` | write |
| `GET` | `/locations/` | read |
| `GET` | `/groups/` | read |
| `GET` | `/categories/` | read |
| `GET` | `/tags/` | read |
| `GET` | `/health/` | none |

Filters on `/parts/`: `status`, `group`, `category`, `tag`, `location`,
`search`, `needs_restock=1`, `part_number__in=a,b,c`. `/groups/` takes
`category`.

**Group is not location.** They are separate fields so a part can be
reclassified without appearing to move, and moved without appearing to be
reclassified.

**Group holds only the fine level.** `Potentiometers`, `Capacitors`, `Diodes`.
The macro level is `Category` on the group, optional and usually empty: most of
the catalog is stock rather than curriculum. What the categories look like,
and which are shown, is the guides site's decision.

**Group is singular, tags are plural.** A part is one kind of thing, which is
what makes `?group=capacitors` an unambiguous answer to "every capacitor below
minimum". Tags carry what a part *also* is: `0386` is a soft potentiometer with
`group=potentiometers` and `tag=touch`.

### Authentication

Service callers send a bearer key:

```
Authorization: Bearer <prefix>.<secret>
```

Create one in the admin under **API keys**. The plaintext is displayed once at
creation and never again; only a SHA-256 hash is stored. Give ioref-web
a `read` key.

> A leak of this repository or its database yields only hashes.

Scopes are `read` (safe methods only) and `write`.

## Human sign-in

`AUTH_MODE` selects the identity source; API keys are unaffected by it.

- `local`: Django's own login. Development and small installs.
- `shib`: mod_shib terminates SAML upstream and passes attribute headers.
- `oidc`: Entra or any OIDC provider (`uv add mozilla-django-oidc`).

Moving from Shibboleth to Entra is an env change plus a proxy reconfiguration,
not an application change; no SAML or OIDC library is imported at module scope.

Accounts are **never created by signing in**. SSO asserts who someone is, never
that they may edit stock, and under `shib` the proxy admits everyone the
institution vouches for. An eppn with no account authenticates as nobody and the
request continues anonymously. Create accounts explicitly:

```bash
uv run python manage.py grant_staff you@andrew.cmu.edu --superuser
uv run python manage.py grant_staff colleague@andrew.cmu.edu
uv run python manage.py grant_staff departed@andrew.cmu.edu --revoke
```

Usernames are eppns, so a bare Andrew ID will never match the SSO headers.
`--revoke` withdraws access without deleting the account, because stock events
record who counted them.

> **`AUTH_MODE=shib` is only safe behind a proxy that overwrites the identity
> headers on every request.** The app trusts them completely. If it is reachable
> without going through that proxy, anyone can authenticate as anyone by setting
> a header. See the deployment section of `CLAUDE.md`.

## Admin theme

`django-unfold`, themed with IDeATe's house palette, which color-codes parts by
category:

| input | output | controller | connector | power |
|---|---|---|---|---|
| `#14B04D` | `#00A0C4` | `#4C265B` | `#636466` | `#DD1B50` |

Input green is the admin primary; base greys are the site's own neutral scale
(`#fdfdfd` → `#1d1d1d`) rather than Unfold's blue-tinted Tailwind slate.
Typography is Nunito Sans.

The stock column decorates only exceptions: crimson for below-minimum, grey for
never-counted, plain number otherwise. Marking healthy rows too makes the list
harder to scan and buries the ones needing work.

The admin's own scales are `UNFOLD["COLORS"]`; a spin-out deployment replaces
them to rebrand. Category colors belong to whatever renders the categories.

## Tests

```bash
uv run python manage.py test
```

## Still to do

- Staff-facing count-entry form. The admin covers it, but a barcode-friendly
  single-field page is the actual daily workflow.
- Part sets allow a part in only one set. Should be many-to-many in ioref-web.
