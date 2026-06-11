# Publishing this repo

Everything you need before `git push`, and the rules for keeping it safe once
it's public.

---

## 1. Pre-flight checklist

Run through this once before the first push.

- [ ] `README.md` — replace `roomtwin-adt` / `roomtwin-rg` if you used different
      resource names, and drop your repo URL into the clone instructions
- [ ] `.env` is **not** tracked — confirm with `git status --ignored`
- [ ] `.env.example` contains placeholders only, no real hostname or key
- [ ] `config/room.yaml` describes the fictional room, not your real one
- [ ] No `__pycache__/`, `.venv/`, or `*.pyc` staged
- [ ] `git log -p | grep -iE "sharedaccesskey|api\.[a-z]+\.digitaltwins|subscription"`
      returns nothing — a secret in an old commit is still public

Then:

```bash
cd server-room-twin
git init
git add .
git commit -m "server-room-twin: a server room digital twin on Azure Digital Twins"
git branch -M main
git remote add origin https://github.com/umairgulfam/server-room-twin.git
git push -u origin main
```

Then add a line to the README of `umairgulfam/Solutions` pointing here, so the
index still lists it.

---

## 2. Do's

**Keep the fictional room as the committed default.**
`config/room.yaml` is the demo. Anyone cloning the repo gets a working room
with four racks and two CRAC units and can run the simulator in thirty seconds.
That is the single biggest thing that makes a repo get used.

**Lead the README with a runnable command.**
Someone landing on the page should see `python simulate.py --duration 600` above
the fold and understand they need no Azure account to try it.

**Show real output.**
The sample run in the README is copied from an actual execution, not invented.
If you retune the model, regenerate it rather than editing the numbers.

**Enable secret scanning and Dependabot.**
Settings → Code security. Both are free on public repos. Secret scanning would
catch an IoT Hub connection string if one ever slips in.

**Tag releases as you finish phases.**
`v0.3.0` at phase 3, `v0.4.0` when the Function lands. It makes the phase table
in the README meaningful rather than aspirational.

**Say what's built and what isn't.**
The status table already does this. Keep it honest — a repo that claims phase 6
and ships phase 3 loses trust immediately.

**Pin your dependency ranges when you tag a release.**
`requirements.txt` uses `>=` today, which is fine for active development.
Freeze it before you tell people a version works.

---

## 3. Don'ts

**Don't commit your real room's layout.**
This is the important one. Rack positions, hostnames, power capacity, cooling
headroom and thermal margins are a reconnaissance package: they tell someone
exactly which rack to attack and how long the room survives without cooling.
Keep the real thing in `config/room.local.yaml` and add that to `.gitignore`.
Pass it with `--config` when you run against reality.

**Don't commit `.env`, and don't paste an instance URL into the README.**
Your ADT hostname is not a secret in the cryptographic sense, but it names a
live endpoint under your subscription. There's no upside to publishing it.

**Don't commit Azure resource IDs, subscription IDs, tenant IDs, or object IDs.**
The `az` commands in the README use placeholder names for exactly this reason.

**Don't rely on `git rm` to remove a leaked secret.**
It stays in history. Rotate the key first, then rewrite history with
`git filter-repo` if you must — but rotation is the part that actually matters.

**Don't hardcode credentials to make the sample "easier to run".**
`DefaultAzureCredential` reading from `az login` is the right pattern and it is
already no harder than a connection string.

**Don't present the thermal model as production-grade.**
It's a lumped energy balance, and the README says so. If you claim CFD accuracy
someone will benchmark it and be right to complain.

**Don't let `config/room.yaml` and the DTDL models drift.**
Add a property to a model without adding it to the config and seeding produces
a twin with a null property. The config is the source of truth for both.

---

## 4. Recommended repo settings

| Setting | Value | Why |
|---------|-------|-----|
| Visibility | Public | Nothing here is sensitive once the checklist passes |
| Secret scanning | On | Catches connection strings before they're indexed |
| Dependabot alerts | On | The Azure SDKs move fast |
| Branch protection on `main` | Optional | Worth it once someone else contributes |
| Topics | `azure-digital-twins`, `digital-twin`, `iot`, `dtdl`, `python`, `data-center` | This is how people find it |

Suggested repo description:

> A live digital twin of a server room on Azure Digital Twins — DTDL models,
> a physics-based simulator, derived thermal metrics, and what-if scenarios.

---

## 5. If you're publishing this as portfolio work

Two things reviewers look for that are easy to miss:

**Explain the modelling decision, not just the code.** The `Asset` base
interface exists so one `contains` relationship can target racks, servers,
sensors and PDUs. That's a design choice worth one sentence in the README —
it shows you understand DTDL inheritance rather than having copied a sample.

**Show the failure case.** Anyone can build a twin that reports temperatures.
The both-CRACs-lost run producing "about an hour" is the part that demonstrates
the twin is doing something a dashboard can't. Put it near the top.
