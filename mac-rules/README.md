# mac-rules — Little Snitch rule-group subscriptions

Little Snitch rules for the Mac seat (**mbp-mda**), managed from git instead of
from the Mac's GUI.

The subscription URL is **`https://rules.mdapi.ch/s/<token>/household.lsrules`**,
where `<token>` is 64 hex chars held in Akeyless — the path is a credential, not
an address (see *Confidentiality* below). Read it when you need it:

```
AKEYLESS_GATEWAY_URL=https://cm.mdapi.ch/akeyless-api/ \
  akeyless get-secret-value --name /mdapi/mac-rules/lsrules-path/token
```

## Why a subscription and not the CLI

Little Snitch 6.5's command line cannot add rules. Its whole surface is
`capture-traffic, debug-categories, export-model, list-preferences, log,
log-traffic, profile, read-preference, recrypt-config, restore-model,
restrictions, rulegroup, update-rule-groups, write-preference`:

- `rulegroup` only **enables/disables** an existing group (`-e` / `-d` by name);
- `export-model` and `restore-model` — the only write paths — **require root**,
  and the seat has no sudo;
- `update-rule-groups` refreshes **factory** groups for installed apps; it is
  *not* a subscription refresh.

A **remote rule group** is the one supported way in: subscribe once in the GUI,
and from then on every change here lands on the Mac on Little Snitch's own
update interval (per-group, set in the group's editor).

## One-time setup on the Mac (needs a human, once)

Rules window → **+** next to *Rule Groups* in the sidebar → **Remote Rule
Group…** → URL

```
https://rules.mdapi.ch/s/<token>/household.lsrules
```

(`<token>` from the Akeyless command at the top — 64 hex chars, no trailing
slash. The URL is stored on the Mac; it is a bearer capability, so don't paste it
into tickets, chat or screenshots.)

→ **Add**. Then, in the group's editor, two things matter:

- ⚠️ **"Disable new Allow-rules" — a decision, not a default to flip.** It is ON,
  and Little Snitch's documented behaviour is that *allow rules are imported
  disabled until you review them* (deny rules import active either way). This
  group is nothing but allows, so leaving it ticked means the subscription looks
  healthy and changes nothing — the same silent success the subscription exists
  to prevent. The trade-off is real:
  - **Ticked = review discipline.** A merge that *adds* an allow lands it
    switched off; a human enables it in the Rules window. One click per new rule,
    in exchange for a human gate on a file that otherwise becomes firewall
    policy unreviewed. **This is the honest setting while more than one account
    can merge into `mac-rules/`.**
  - **Unticked = hands-off.** Defensible only because `lint.py` check G refuses
    the shapes that matter — a published `allow` cannot be `remote: any`, cannot
    omit `ports`, and must name a remote scope — so the worst a hostile merge can
    add is a LAN-scoped port. Read "Protecting the rules" below before choosing
    this.
- Set the **update interval** — that is what decides how quickly a merge here
  reaches the Mac.

If the status dot is grey rather than green, the group itself is disabled: tick
its checkbox, or `littlesnitch rulegroup -e "mdapi household (mbp-mda)"`.

⚠️ Enabling rules is the approval step in Little Snitch's threat model — the
publisher of a rule file could otherwise grant itself network access. Only
subscribe to a URL you control the server for.

Requirements Little Snitch enforces, so don't fight them:

- **https with a publicly-trusted certificate.** Self-signed and private-CA
  certs are rejected; `letsencrypt-prod` is fine.
- The body is **JSON** (`.lsrules`), decoded as a rule group. The format is
  documented at
  <https://help.obdev.at/littlesnitch5/ref-lsrules-file-format>.
- A failed fetch keeps the **old** rules and raises `subscribedRuleGroupUpdateFail`
  in the LS log — a silent stale group is the failure mode to watch for.

## Confidentiality: the URL is a credential

Little Snitch's fetch is **anonymous**. The dialog takes a URL and nothing else —
no username, no password, no headers, no client certificate — and the help page
suggests hosting rule groups on `github.com` raw links or a `gitlab.com` snippet,
which is the shape of the feature: a public file at a public URL. So the only
access-control surface that exists is **the URL itself**.

What the file reveals, if it is readable: the Mac's role, the service behind each
port, and the ports it accepts from the local network — a hint sheet for anyone
who is already on the same LAN. That is worth hiding from scanners, search
engines and randoms, and it is what this bundle does:

| lever | how |
|---|---|
| unguessable path | `location = /s/${LS_TOKEN}/household.lsrules`, token = 64 hex chars generated once in Akeyless (`/mdapi/mac-rules/lsrules-path/token`), pulled by ESO into the `lsrules-path` Secret and injected at container start with `envsubst` (`NGINX_ENVSUBST_FILTER` keeps it to that one variable) |
| no accidental disclosure in the config | the template is in the ConfigMap, the token is in the Secret — the rendered `default.conf` inside the pod is the only place both exist. Never commit the token; never put it in the JSON `description`, which is served |
| nothing to enumerate | every other path — including the old public `/household.lsrules` — returns a bare 404 |
| no caching or indexing | `Cache-Control: no-store`, `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer` |
| no logging of the token | nginx `access_log off`, and `nginx.ingress.kubernetes.io/enable-access-log: "false"` on the Ingress — otherwise every fetch writes the token to Loki |

What it does **not** do: it is a bearer capability, not authentication. Anyone who
sees the URL once can read the file until you rotate it: a screenshot, a screen
share, the Mac's own Little Snitch config, a proxy log. Treat it like a password,
and rotate it rather than share it.

Rejected alternatives, so they are not re-proposed:

- **HTTP basic auth / headers / mTLS** — the fetch has no way to send them.
- **Restrict the Ingress to LAN + WireGuard** — the Mac roams, and a fetch that
  fails is indistinguishable from "no change": Little Snitch keeps the old rules,
  so the control fails into silent staleness.
- **Encrypt or sign the body** — Little Snitch needs plaintext JSON and has no
  key management. TLS is the only integrity story (see the threat model below).

Rotating the token (the old URL dies as soon as the pod re-renders):

```
# 1. new value in Akeyless (single line — no --multiline needed here)
NEW=$(openssl rand -hex 32)
AKEYLESS_GATEWAY_URL=https://cm.mdapi.ch/akeyless-api/ \
  akeyless update-secret-val --name /mdapi/mac-rules/lsrules-path/token \
  --value="$NEW" --keep-prev-version true
# 2. make ESO sync now instead of waiting out refreshInterval (1h), and WAIT for
#    it: the order matters, a restart before the sync only re-renders the old one
kubectl -n mac-rules annotate externalsecret lsrules-path force-sync=$(date +%s) --overwrite
until [ "$(kubectl -n mac-rules get secret lsrules-path -o jsonpath='{.data.token}' | base64 -d)" = "$NEW" ]; do sleep 5; done
# 3. nginx rendered the config at container start — force the re-render
kubectl -n mac-rules rollout restart deploy/lsrules
# 4. on the Mac: edit the group's URL (add it again if the field is read-only),
#    then confirm the new path answers 200 and the old one 404s.
```

## Protecting the rules from attackers

Once subscribed, **this file is firewall policy on mbp-mda, applied without
review**. A hostile rule is not a subtle thing:

| a hostile rule | what it buys |
|---|---|
| `allow` | an open door — an inbound port reachable by the LAN or the internet, for any process. Imported rules are ordinary rules; nothing in the UI says "this came from git" |
| `deny` | blindness — silence outbound alerting or telemetry, break DNS or software updates, and the Mac looks fine |
| `ask` with many matches | popup fatigue, trained clicks |

The trust chain, and where it actually gives way:

| link | held by |
|---|---|
| who can change the file | `mdapi/fleet` `main`: push = Maintainers, **merge = Developers**; no approval rule, no `CODEOWNERS`. One Developer account is enough to publish a rule |
| what a change may say | `lint.py` check **G** (`[LSRULES-BOUNDED]`): allows must be port-scoped, must name a remote scope, may not use `remote: any` |
| the cluster | anyone who can write ns `mac-rules` can patch the ConfigMap directly; Fleet only reverts that on the next git change |
| the transport | `https` with a publicly-trusted cert, which Little Snitch validates — so an attacker needs a valid cert for `rules.mdapi.ch`. CAA already pins that: `mdapi.ch` carries `issue "letsencrypt.org"` + `issuewild "letsencrypt.org"`, so no other CA may issue for it. ⛔ That leaves **DNS** as the way in: the public zone accepts `nsupdate` under a TSIG key, and whoever holds it can repoint `rules.mdapi.ch` at a server of their own and still pass Let's Encrypt's validation — a valid cert from a server you do not control. The zone's update key sits in the trust path at the same level as git |
| what the Mac does | imports the rules; on a failed fetch it keeps the **previous** ones and only logs `subscribedRuleGroupUpdateFail` |

Ordered by value per unit of effort:

1. **Keep the review safeguard on** unless the constraint below is what you want
   to rely on (see the one-time setup section).
2. **Protect the DNS zone as hard as git.** CAA is already correct, so the
   remaining lever is who can run `nsupdate` (the TSIG `mdapi` key) and who
   controls the registrar. Rotate that key if it has ever been pasted somewhere
   durable, and lock the registrar account. A hijack here is invisible: the Mac
   fetches over valid TLS and never asks again.
3. **Tighten git, not the cluster.** The weak link is merge rights on `main`, not
   TLS: add an approval rule (or restrict merge to Maintainers) so publishing a
   rule needs a second person.
4. **Keep the served set minimal and port-scoped.** Check G makes the bound
   structural: a compromised merge can at worst add a LAN-scoped port.
5. **Know the kill switch.** Reverting in git is the durable fix but waits for
   the interval; the immediate stop is **disabling the group on the Mac**
   (`littlesnitch rulegroup -d "mdapi household (mbp-mda)"`, or untick it).
   ⛔ Breaking the endpoint does *not* stop anything — a failed fetch keeps the
   old rules.
6. **Keep the payload non-sensitive anyway.** The token keeps scanners out, not
   a determined reader, and the JSON is plaintext to every subscriber: ⛔ no
   customer hostnames, no internal addresses, no hints about what else runs
   where — `notes` and `description` included.
7. **The content watch is built** (`lsrules-watch-cron.yml` + `lsrules-watch-cm.yml`,
   alerts in `monitoring-rules/65-mac-rules.yml`). Little Snitch has no signature
   or hash pin, so nothing on the Mac can tell a git-merged file from a
   DNS-hijacked one; this is the outside view that can. Details below.


Drift is the other quiet failure: what the Mac gets is what Fleet last applied
from git, so a direct `kubectl edit` reaches the Mac but not the repo, and the
next merge silently reverts it. Compare the three copies (identical hashes mean
git, cluster and wire agree):

```
TOKEN=$(AKEYLESS_GATEWAY_URL=https://cm.mdapi.ch/akeyless-api/ \
  akeyless get-secret-value --name /mdapi/mac-rules/lsrules-path/token)
curl -sS "https://rules.mdapi.ch/s/$TOKEN/household.lsrules" | sha256sum
kubectl -n mac-rules get cm lsrules -o json | jq -j '.data["household.lsrules"]' | sha256sum
python3 -c 'import yaml,sys;sys.stdout.write(yaml.safe_load(open("fleet/mac-rules/lsrules-cm.yml"))["data"]["household.lsrules"])' | sha256sum
```

(`jq -r` would add a newline and change the hash — `-j` is deliberate.)

## The content watch

`CronJob/lsrules-watch` runs at :13 and :43, fetches the subscription URL through
**three resolvers** — the in-cluster one, the bpi-r4 dnsmasq at `192.168.1.254`
(what mbp-mda asks first) and `1.1.1.1` (what the world gets) — and hashes each
body against the `lsrules` ConfigMap mounted into the pod. For each leg it also
pins SNI to `rules.mdapi.ch` while connecting to the address *that resolver*
returned, so a resolver answering with somebody else's server is what the check
is built to catch.

Why the legs exist, measured 2026-10-22:

```
getent hosts rules.mdapi.ch        → 192.168.1.191   (cluster / dnsmasq view)
dig @1.1.1.1 rules.mdapi.ch        → 31.3.128.50     (public view)
```

A check that used only the pod's resolver would have watched a different answer
from the one every other client gets. All three currently serve identical bytes.

**What it compares, and what it does not.** The expected side is the *cluster's*
ConfigMap, not the git blob: Fleet owns git → cluster and reports that hop in the
`mdapi-prod-mac-rules` bundle, while this watch owns cluster → wire, which Fleet
cannot see. A green watch therefore means "every resolver's answer serves what
the cluster holds", not "git is fine".

**Series** (`job="lsrules_watch"`) — `lsrules_watch_content_matches{leg}`,
`..._fetch_ok{leg}`, `..._http_status{leg}`, `..._last_run_timestamp_seconds`,
`..._last_run_success`. Alerts:

| alert | fires when | severity |
|---|---|---|
| `MacRulesContentMismatch` | a leg **answered** (fetch_ok 1) with bytes that are not the ConfigMap's, 15 min | critical |
| `MacRulesWatchFetchFailing` | a leg could not resolve or connect at all, 3 h | warning |
| `PushgatewayGroupStale` | the group is older than its declared 90 min ceiling — the watch itself died | warning |

The two are kept apart on purpose: an unreachable leg also carries
`content_matches 0`, and calling that "served different bytes" would send you
looking for a hijack the metrics say did not happen.

**Test it** (negative controls were run 2026-10-22 in a throwaway namespace, all
fired):

```
# happy path
kubectl -n mac-rules create job --from=cronjob/lsrules-watch manual-1
kubectl -n mac-rules logs job/manual-1

# "wire serves what git never published": change the expected side, not the wire
#   -> content_matches 0 on every leg, MacRulesContentMismatch
# "a leg is dead": LS_PUBLIC_RESOLVER=192.0.2.1 in the CronJob env
#   -> fetch_ok 0 on that leg, MacRulesWatchFetchFailing, mismatch stays silent
# "token rotated, Mac not updated": point LS_TOKEN_FILE at 64 wrong hex chars
#   -> http_status 404 on every leg, MacRulesContentMismatch
```

⛔ The script is Python in a ConfigMap, so `lint.py` cannot see it — the controls
above are its test, and any edit to `lsrules-watch-cm.yml` needs one of them
re-run. The push job label is `LS_JOB`-overridable precisely so a smoke run
cannot be mistaken for a real one; delete the smoke group afterwards
(`curl -X DELETE http://pushgateway.monitoring.svc:9091/metrics/job/<job>`).

## What is in `household.lsrules`

| rule | why |
|---|---|
| allow incoming TCP **1240** from `local-net`, any process | the mflux image shim (litellm `image-household`). Port-keyed deliberately: a path-keyed rule rots on every Homebrew patch bump |
| allow incoming TCP **1251** from `local-net`, any process | probe port, so the per-binary inbound gate can be re-tested from the pod without editing this file |

Both are `priority: high` so they win over any older deny.

## Changing the rules

1. Edit `lsrules-cm.yml` (keep it valid JSON inside the block scalar).
2. Open an MR. CI runs `lint.py`; check **G** (`[LSRULES-BOUNDED]`) fails an
   allow that is unbounded (`remote: any`, no `ports`, no remote scope) or
   malformed, and warns on a versioned Homebrew `process` path. Fleet reconciles
   on merge — ⛔ never `kubectl apply` this.
3. On the Mac: wait for the interval, or re-open the group's editor to force a
   refresh. Verify against the token URL (see *Verify the endpoint*).
   ⚠️ Editing the **template** (`default.conf.template`) needs a new pod: nginx
   renders it once at container start. Reloader (stakater, cluster-wide
   `--auto-reload-all`) rolls the Deployment when the ConfigMap changes, and the
   `lsrules/config-rev` annotation is the lever that works without it — bump it
   anyway, so the change does not depend on one component being healthy.

## Verify the endpoint

```
TOKEN=$(AKEYLESS_GATEWAY_URL=https://cm.mdapi.ch/akeyless-api/ \
  akeyless get-secret-value --name /mdapi/mac-rules/lsrules-path/token)
curl -sS -o /dev/null -w '%{http_code}\n' https://rules.mdapi.ch/healthz
curl -sS "https://rules.mdapi.ch/s/$TOKEN/household.lsrules" | jq -r '.name, (.rules|length)'
# the old public path must be dead, and so must a wrong token:
curl -sS -o /dev/null -w '%{http_code}\n' https://rules.mdapi.ch/household.lsrules
curl -sS -o /dev/null -w '%{http_code}\n' https://rules.mdapi.ch/s/deadbeef/household.lsrules
```

That is the manual version of what the content watch does every 30 minutes — the
watch exists so nobody has to remember to run it.

