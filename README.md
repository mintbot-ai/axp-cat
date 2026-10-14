# axp.cat

The public home of the [Agent Extension Protocol (AXP)](https://github.com/mintbot-ai/agent-extension):
an introduction to the standard and the open catalog of packages that pass it.

- **https://axp.cat** - the site
- **https://axp.cat/catalog** - the catalog; `/api/catalog.json` is the same list for machines
- **https://axp.cat/submit** - list a package

## The gate

Anyone can submit a package; nothing is listed until it passes the gate in
[`axpcat/gate.py`](axpcat/gate.py), which walks the same path an AXP host does:

1. the locator (a manifest URL, a publisher domain or a GitHub repository URL)
   resolves to a manifest per SPEC 3.1;
2. the `axp` reference implementation validates it in full;
3. it is signed and the signature verifies against the key it carries;
4. that key is bound to `identity.publisher` - the manifest was served from the
   publisher's own domain, or the publisher's key directory
   (`/.well-known/agent-extension-keys.json`) lists the key for the extension.
   A code forge is never anybody's origin, so GitHub-hosted manifests are
   listed through the key directory only;
5. the release is not past its `valid_until`.

The gate judges form, signature and publisher binding, not quality or safety.
Listings are re-checked against their locator with `axpcat refresh`; a listing
whose locator stops passing keeps its last good release and shows the error.

Publishers can run the gate without listing anything:

```bash
axpcat check https://github.com/you/my-ext
```

## Running it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
python -m pytest
AXPCAT_DB=/tmp/catalog.db uvicorn --factory axpcat.app:create_app --port 8010
```

Settings come from the environment (`axpcat/config.py`): `AXPCAT_PUBLIC_BASE`,
`AXPCAT_DB`, `AXPCAT_FETCH_TIMEOUT`, `AXPCAT_SUBMIT_PER_HOUR`.

Outbound fetches go through [`axpcat/fetch.py`](axpcat/fetch.py): https only,
public addresses only (checked before connecting and pinned for the connection),
redirects re-checked, bodies capped. The service never needs more than
read-only access to its own code and a writable directory for the SQLite file.

## Deployment

axp.cat runs on the mintbot.ai central servers (`dev.axp.cat` follows `main`
automatically; `axp.cat` is promoted by hand). The service unit, the nginx
vhost and the certificate bootstrap live in the mintbot repository, which pins
this repository at a commit; this repository holds only the application.

## Licence

MIT, like the protocol itself.
