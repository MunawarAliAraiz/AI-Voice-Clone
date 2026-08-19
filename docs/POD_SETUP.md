# Pod setup — running the backend on a RunPod GPU

Assumes a fresh RunPod pod with an NVIDIA GPU (developed on an RTX A5000 / A4500, 20–24 GB), Ubuntu,
and Python 3.12.

## The one command

**On your own laptop, `cd` into this repo first** — the command ends with
`< scripts/pod-bootstrap.sh`, a path relative to the repo root, and it pipes your *local* copy of the
script into the pod's shell. Running it from anywhere else fails with `No such file or directory`.

```bash
cd /path/to/AI-Voice-Clone
```

Then, from that directory — this is the whole deployment, provisioning through running server.

**Public deployment (ngrok + your deployed frontend):**

```bash
ssh root@<HOST> -p <PORT> "START=1 NGROK_AUTHTOKEN=<token> NGROK_DOMAIN=<your-name>.ngrok-free.dev FRONTEND_URL=https://<your-app>.workers.dev bash -s" < scripts/pod-bootstrap.sh
```

**Local frontend against the pod backend (no ngrok, no public URL):** provision and start the backend,
then reach it over an SSH tunnel from your laptop. You don't need `NGROK_*` or `FRONTEND_URL` — a
tunnelled request is same-origin from the browser's view, so CORS never applies:

```bash
ssh root@<HOST> -p <PORT> "START=1 bash -s" < scripts/pod-bootstrap.sh
```

Then, on your laptop, tunnel the pod backend and point the dev server at it (see
[Connect a local frontend](#connect-a-local-frontend-ssh-tunnel) for the full flow):

```bash
ssh -N -L 8010:127.0.0.1:8000 -p <PORT> -i ~/.ssh/id_ed25519 root@<HOST>
```

```bash
cd frontend && VITE_PROXY_TARGET=http://127.0.0.1:8010 npm run dev
```

Port `8010` (not `8000`) sidesteps a local backend you may already be running on `8000`. Paste the
pod's `VCS_API_KEY` (printed by the bootstrap) into the frontend's settings gear — the pod mints its
own key, distinct from any local one.

The public form provisions everything, then starts the backend and ngrok and polls until `/api/health`
answers, so there is no second step. It ends by printing your API key and a status block:

```
== 20. current status ==
   backend:  UP   (127.0.0.1:8000)
   ngrok:    UP   https://<your-name>.ngrok-free.dev
   public:   OK   https://<your-name>.ngrok-free.dev/api/health -> 200
```

Three `UP`/`OK` lines mean it is fully live. Anything else tells you which half is broken:
`backend: DOWN` is the server, `public: FAIL ... 404` is the tunnel not connected, `502` is the tunnel
up but the backend refusing.

**After the first run it gets shorter.** `NGROK_DOMAIN` and `FRONTEND_URL` are remembered on
`/workspace`, so a later restart on the same volume only needs:

```bash
ssh root@<HOST> -p <PORT> "START=1 NGROK_AUTHTOKEN=<token> bash -s" < scripts/pod-bootstrap.sh
```

You do **not** need `GH_USER` / `GH_TOKEN` in any of these. The repo is public for read, so the pod
clones anonymously; those two are only for pushing commits *from* the pod, which a deploy never does.
Passing them writes your token to `/root/.git-credentials` on the pod for no benefit.

`FRONTEND_URL` is your deployed frontend's origin, and it is what makes CORS work. **Pass it once** —
the script saves it to `/workspace/vcs-frontend-url` and reuses it on every later run, so you cannot
accidentally restart the backend with a placeholder origin. Add `START=1` and it also starts the
backend and ngrok for you, so the whole deployment is genuinely one command.

Getting this wrong fails in a misleading way: the browser reports `No 'Access-Control-Allow-Origin'
header is present` and the UI's status chip reads **offline**, which looks like the backend is down
when it is actually up and rejecting the origin. The value must match the deployed frontend exactly —
scheme included, no trailing slash. `https://your-app.workers.dev` and `https://your-app.pages.dev` are
different origins, and Cloudflare gives you one or the other depending on which product you deployed
under.

That's it for a fresh pod. Piping the script in over `"bash -s" <` isn't backgrounded, so every line
it prints streams to your terminal live as it runs — `uv sync`, the torch install, the weight
download, the test run — nothing extra needed to watch it. On a cold `/workspace` it takes **15–30
minutes** (~15 GB of weights across four model sets), and ends by printing **your `VCS_API_KEY` and
`VCS_MEDIA_TOKEN_SECRET` in full**, plus the exact command to start serving. Copy the key into the
frontend's settings gear, run the command, and the backend is up.

> **The streaming form dies with your SSH connection.** That is the cost of watching it live: the
> bootstrap is a child of the SSH session, so a dropped connection — `client_loop: send disconnect:
> Connection reset`, a closed laptop, flaky wifi — kills it partway through, mid-`pip install`.
> Observed on a real run that died at step 6 of 20. Re-running is safe (every step is idempotent),
> but on a slow link, or if you want to walk away, run it **detached on the pod** instead so only
> your *view* of it can disconnect:
>
> ```bash
> # copy it over, then run it under tmux on the pod
> ssh root@<HOST> -p <PORT> "cat > /workspace/pod-bootstrap.sh" < scripts/pod-bootstrap.sh
> ssh root@<HOST> -p <PORT> "tmux new-session -d -s bootstrap \
>   'START=1 NGROK_AUTHTOKEN=<token> NGROK_DOMAIN=<domain> FRONTEND_URL=<url> \
>    bash /workspace/pod-bootstrap.sh 2>&1 | tee /workspace/bootstrap.log'"
>
> # then watch from anywhere, reconnect-safe:
> ssh root@<HOST> -p <PORT> "tail -f /workspace/bootstrap.log"
> ```
>
> `grep '^== ' /workspace/bootstrap.log | tail -5` gives you just the step headers if you only want
> to know how far along it is.

`-i ~/.ssh/id_ed25519` is only needed if you're not relying on an ssh-agent to offer the key
automatically. On Windows, use `C:\Windows\System32\OpenSSH\ssh.exe` instead of Git Bash's `ssh` if
the key is passphrase-protected and loaded in the Windows ssh-agent — Git Bash can't see that agent.

You do **not** need `GH_USER` / `GH_TOKEN` to clone — the repo is public for read. Their main purpose
is pushing commits *from* the pod, which most sessions never do:

```bash
ssh root@<HOST> -p <PORT> "GH_USER=<you> GH_TOKEN=<token> bash -s" < scripts/pod-bootstrap.sh
```

**If step 3 fails with `fatal: could not read Username for 'https://github.com'`,** the anonymous
clone was rejected — observed once, from a RunPod IP, with a plain `401` and `Repository not found`
on GitHub's git-upload-pack endpoint despite the repo being genuinely public. Not confirmed whether
this is RunPod-specific, IP-reputation-based, or a one-off; treat "no token needed" as the common case,
not a guarantee. The same `GH_USER=<you> GH_TOKEN=<token>` command above fixes it — a token
authenticates past whatever anonymous git-over-HTTPS was hitting, even though the repo needs no
special access. A classic PAT with just the `repo` scope is enough.

**What that command actually does**, so you know what to expect and where to look if something fails:

- Redirects HuggingFace/torch/pip/uv caches onto `/workspace` — the pod's `/` is a ~30 GB overlay that
  fills and kills the pod otherwise.
- Clones the repo to `/workspace/AI-Voice-Clone` (or pulls, if it's already there from a previous
  session on this same pod).
- Installs `uv` and `ffmpeg` if missing.
- Builds **six** separate Python environments — this is a structural requirement of the project, not
  incidental: `import torch` must never be reachable from the API process, so each GPU model runs in a
  **separate interpreter** the API talks to over a wire protocol. `uv sync` builds the API's env (no
  torch); then one `uv venv` per runtime, each pinned to the **cu128** torch build — a plain
  `pip install voxcpm` pulls a cu130 wheel whose CUDA runtime is newer than the RunPod driver, which
  makes `torch.cuda.is_available()` silently `False` and every generation quietly falls back to CPU
  and times out.

  | venv | For | Needed because |
  |---|---|---|
  | `.venv` | the API itself | no torch, by design |
  | `.venv-voxcpm` | `voxcpm2`, `voxcpm2_urdu_arabic` | English + Roman Urdu — the default route |
  | `.venv-omnivoice` | `omnivoice_urdu` | Perso-Arabic Urdu; verified, picked by name |
  | `.venv-chatterbox` | `chatterbox_ml_v3` | not routable — kept warm for future work |
  | `.venv-qwen` | Speech Direction's LLM analyzer | the "Let AI suggest emotion/tone" button |
  | `.venv-eval` | `eval/` harness | Whisper CER + ECAPA cosine; never the API |

- Downloads every pinned weight set into the HF cache on `/workspace`: VoxCPM 2 (~7 GB), Chatterbox,
  OmniVoice (~1.2 GB), and Qwen2.5-3B (~6 GB). **Budget ~80 GB of volume** — a fully bootstrapped pod
  measured 76 GB. Note `df` lies about this mount (it reports the whole MooseFS cluster); use
  `du -sh /workspace`.
- Generates `VCS_API_KEY` and `VCS_MEDIA_TOKEN_SECRET` **once** into `/workspace/vcs-secrets.env`
  (mode `600`) and reuses that file on every later run, so the values are stable across pod restarts.
  See [Generating and reusing secrets](#generating-and-reusing-secrets) for why stability matters.
- Runs the CPU test suite as a sanity check.
- If `NGROK_AUTHTOKEN` is set, installs and configures ngrok too (see [Public deployment](#public-deployment-ngrok--cloudflare) below).

**Re-running the same command on the same pod is safe.** Every step checks whether its work already
exists before doing it again: cache dirs use `mkdir -p`, the repo step detects an existing checkout and
`pull`s instead of cloning, `uv sync` and `uv venv` are idempotent by design, the weights step relies on
HuggingFace Hub's own cache to skip a re-download of the same pinned revision, and `ngrok config
add-authtoken` just overwrites the same token. Nothing gets duplicated or corrupted by running it twice
— including immediately after a run that failed partway through.

**Windows: if the script fails instantly with `set -euo pipefail` or `invalid option name`,** it's not
the script — it's CRLF line endings. Git on Windows commonly checks files out with `core.autocrlf=true`,
which silently turns the script's LF line endings into CRLF on disk; piping that copy into
`"bash -s" < script` then sends literal `\r` bytes into the remote bash, which chokes on line 1. This
repo's `.gitattributes` pins `*.sh` to LF on checkout, so a fresh `git clone` after that file exists
won't hit it — but if your working copy predates it, force a re-checkout:

```powershell
Remove-Item scripts\pod-bootstrap.sh
git checkout scripts\pod-bootstrap.sh
```

Verify it worked — this should **not** mention CRLF:

```powershell
Get-Content scripts\pod-bootstrap.sh -Raw | Select-String "`r`n" | Measure-Object
```

(A `Count` of `0` means no CRLF; `file scripts/pod-bootstrap.sh` from Git Bash works too and says
"with CRLF line terminators" only when the problem is present.)

**Running the one-command bootstrap from PowerShell, not Git Bash:** the `< scripts/pod-bootstrap.sh`
redirect in the command at the top of this doc is bash syntax — PowerShell rejects it outright with
`The '<' operator is reserved for future use`. The obvious fix, `Get-Content ... -Raw | ssh ...`, has
its own trap: PowerShell's pipeline re-encodes text through an external command as UTF-8 **with a BOM**,
even when the source file has none, and that BOM lands before `#!/usr/bin/env bash` on the far end —
`bash: line 1: <BOM>#!/usr/bin/env: No such file or directory`. `cmd /c type file | ssh ...` has the
same problem. The reliable way from PowerShell is `scp` the file over as raw bytes, then run it
remotely — no text pipeline involved:

```powershell
scp -P <PORT> -i ~/.ssh/id_ed25519 scripts\pod-bootstrap.sh root@<HOST>:/tmp/pod-bootstrap.sh
ssh -p <PORT> -i ~/.ssh/id_ed25519 root@<HOST> "bash /tmp/pod-bootstrap.sh"
```

(Prefer Git Bash for the plain `< scripts/pod-bootstrap.sh` form when you have it — it avoids this
entirely. This `scp` two-step is for when you're in PowerShell specifically, e.g. because the SSH key
is passphrase-protected and only the Windows ssh-agent — which Git Bash can't see — has it unlocked.)

---

## Start / stop / restart — `ctl.sh`

Bootstrap writes two files: `/workspace/serve.sh` (the launcher, with secrets and CORS origin baked
in) and `/workspace/ctl.sh` (its lifecycle wrapper). Prefer `ctl.sh` — it is idempotent and won't
double-start:

```bash
/workspace/ctl.sh up        # start the backend ONLY if it isn't already up (safe default)
```

```bash
/workspace/ctl.sh restart   # stop then start
```

```bash
/workspace/ctl.sh stop      # stop the backend
```

```bash
/workspace/ctl.sh status    # is the backend (and ngrok) up?
```

`up` is the one to reach for when you just want to be sure it's running: if the backend is already
healthy it prints `backend already running` and does nothing; if it's down it starts it and polls
`/api/health` until it answers. Run these as SSH one-liners from your laptop, e.g.
`ssh root@<HOST> -p <PORT> -i ~/.ssh/id_ed25519 "/workspace/ctl.sh up"`.

`ctl.sh` manages only the backend. ngrok is started by `START=1` on the bootstrap (below), or by hand
(see [Starting ngrok](#starting-ngrok)).

Under the hood `ctl.sh` runs `/workspace/serve.sh` in a detached `tmux` session named `backend`, so it
survives your SSH session closing. To run the launcher directly instead:

```bash
tmux new-session -d -s backend '/workspace/serve.sh 2>&1 | tee /workspace/backend.log'
```

Or skip all of this by passing `START=1` to the bootstrap, which starts backend and ngrok itself.

`serve.sh` sources `/workspace/vcs-secrets.env` rather than inlining
`VCS_API_KEY=$(python -c 'secrets...')`, and that is the whole point: the inline form mints a brand-new
key on every launch, never shows it to you, and silently 401s every frontend that had the previous one
saved. Baking the CORS origin in the same file prevents the matching failure on the other side — a
restart that quietly reverts to a placeholder origin, leaving the backend up but rejecting its own
frontend.

`VCS_WARM_ON_STARTUP=voxcpm2` starts loading the model in the background the moment the process boots,
instead of leaving the first real `/generate` to pay the ~20–60 s cold-load cost. `/api/health`
answers immediately either way — the model just happens to already be resident by the time someone
uses the app, rather than making the first request wait.

Run **one** uvicorn worker. N workers means N schedulers, each believing it owns the whole VRAM budget.

To keep it running after you close the SSH session, launch it detached — either `tmux` (below, also
used for ngrok) or `setsid`:

```bash
tmux new-session -d -s backend '/workspace/serve.sh 2>&1 | tee /workspace/backend.log'
```

Confirm it's up, on the pod:

```bash
curl -s http://127.0.0.1:8000/api/health
```

### Watching logs from your own machine

The `tee /workspace/backend.log` in the detached command above means every request and error is
written to that file, live, independent of the `tmux` session or your SSH connection. From your own
laptop, in your own terminal:

```bash
ssh -p <PORT> -i ~/.ssh/id_ed25519 root@<HOST> "tail -f /workspace/backend.log"
```

`Ctrl+C` stops watching — it does not touch the backend, since `tail -f` is a separate process from
the one actually running the server.

To attach to the running session itself, rather than just its log output:

```bash
ssh -p <PORT> -i ~/.ssh/id_ed25519 root@<HOST> -t "tmux attach -t backend"
```

`Ctrl+B` then `D` detaches without killing it. Detaching this way is not optional if you attach — a
plain `Ctrl+C` from inside the attached session kills the backend, not just your view of it.

---

## Resuming after a pod restart

A pod **stop/start** keeps `/workspace` (both venvs, the weights cache, `backend/data/` with your
voices + history) but wipes `/`, and kills every running process. So the code and weights are still
there — you just need to restart the backend (and ngrok, if you're using it):

`/workspace` survived, so `serve.sh`, `ctl.sh`, the secrets file and the saved frontend origin all did
too — which means the restart reuses the same API key and the same CORS origin rather than drifting:

```bash
/workspace/ctl.sh up
tmux new-session -d -s ngrok 'ngrok http --domain=<your-name>.ngrok-free.dev 8000'
```

You do not need to re-run the full bootstrap script — nothing it built was lost. Re-running it anyway
is harmless (every step is a no-op if its work already exists) if you're not sure what state the pod
is in.

A **new** pod (fresh `/workspace`, not just a restart) has none of this — that's when you run the full
bootstrap command from the top of this doc again.

### Onto a brand-new volume without re-pairing every browser

A fresh `/workspace` has no secrets file, so bootstrap would generate a new `VCS_API_KEY` and everyone
would have to paste it again. Carry the existing pair across instead:

```bash
ssh root@<NEW_HOST> -p <NEW_PORT> "START=1 NGROK_AUTHTOKEN=<token> \
  NGROK_DOMAIN=<your-name>.ngrok-free.dev FRONTEND_URL=https://<your-app>.workers.dev \
  VCS_API_KEY=<existing> VCS_MEDIA_TOKEN_SECRET=<existing> bash -s" < scripts/pod-bootstrap.sh
```

Pass **both or neither** — the script exits rather than accept half a pair. Supplying only the API key
would leave the media secret to be regenerated, which invalidates every signed `?t=` audio URL already
handed out: audio that worked a moment ago starts 403ing, with the API key looking innocent.

Because the ngrok domain is static and carried over too, nothing on the Cloudflare side changes — no
rebuild, no `BACKEND_ORIGIN` edit. Only your `ssh` host and port differ.

---

## Connect a local frontend (SSH tunnel)

For reaching the backend yourself, from your own machine — not for sharing a link with anyone else.
The server binds to `127.0.0.1` on the pod, not the public internet, so reach it with an SSH tunnel.
Run this **on your own machine** and leave it open — forward a **local** port `8010` to the pod's
`8000`, so a local backend you might already be running on `8000` doesn't collide:

```bash
ssh -N -L 8010:127.0.0.1:8000 -p <POD_PORT> root@<POD_HOST>
```

Then start the UI locally, pointing its dev proxy at the tunnel. `vite.config.ts` reads
`VITE_PROXY_TARGET` (default `http://127.0.0.1:8000`); the browser only ever talks to the vite server,
which proxies `/api` server-side, so it stays same-origin and **CORS never applies** — which is why
this works even though the pod's `VCS_CORS_ORIGINS` doesn't list `localhost`:

```bash
cd frontend && npm install && VITE_PROXY_TARGET=http://127.0.0.1:8010 npm run dev
```

Open http://localhost:1420, then **paste the pod's `VCS_API_KEY` into the settings gear** — the
bootstrap prints it, and it lives in `/workspace/vcs-secrets.env` on the pod. The pod mints its own
key (distinct from any local backend's), so requests 401 until it's pasted.

(On Windows, use `C:\Windows\System32\OpenSSH\ssh.exe` if your key lives in the Windows ssh-agent —
Git Bash's `ssh` can't see it.) RunPod assigns a new host:port on every restart, so the tunnel command
changes each time.

---

## Public deployment (ngrok + Cloudflare)

For sharing a working link with other people, not just reaching the backend yourself. Two more pieces
are needed beyond the tunnel above: a public, **stable** URL for the backend (SSH tunnels aren't
shareable, and RunPod's own proxy URL changes every pod restart), and a public frontend that points at
it.

This deployment is: **frontend on Cloudflare Workers, backend on the pod behind an ngrok static
domain, protected by `VCS_API_KEY`.**

### One-time setup

**Backend — claim a static ngrok domain** (free tier gives one permanent domain per account, so this
survives every pod restart):

1. https://dashboard.ngrok.com/get-started/your-authtoken — copy the authtoken.
2. https://dashboard.ngrok.com/cloud-edge/domains — claim a free domain, e.g.
   `your-name.ngrok-free.dev`.

Pass `NGROK_AUTHTOKEN` (and optionally `NGROK_DOMAIN`) to the bootstrap script and it installs and
configures ngrok as part of the same run:

```bash
ssh root@<HOST> -p <PORT> "NGROK_AUTHTOKEN=<token> NGROK_DOMAIN=<your-name>.ngrok-free.dev bash -s" \
  < scripts/pod-bootstrap.sh
```

**Frontend — deploy `frontend/` to Cloudflare.** The dashboard offers two different products under
similar names — check which fields you're shown:

- **Classic Pages** (Build command / Output directory / Root directory): output directory
  `frontend/dist`, root directory `frontend`, build command `npm install && npm run build`.
- **Workers Builds** (Build command / Deploy command / Path) — what this project is actually set up
  for, via `frontend/wrangler.toml`:
  - Path: `frontend`
  - Build command: `npm install && npm run build`
  - Deploy command: `npx wrangler deploy`

**Do not set `VITE_API_BASE`.** It used to be required, pointing the bundle straight at the ngrok
domain. It must now be **removed** from the Cloudflare build settings: `frontend/worker.js` proxies
`/api/*` to the pod, so the frontend calls its own origin, which is already the production default in
`getApiBase()`. Setting `VITE_API_BASE` overrides that and sends requests cross-origin again,
reinstating both the interstitial and the CORS requirement.

The backend origin lives in `wrangler.toml` under `[vars] BACKEND_ORIGIN` — update it there when the
ngrok domain changes.

The app's settings gear has **no Backend URL box** — it was removed for the same reason. Same-origin is
the only base that can be correct once the Worker proxies, and because localStorage outlives a deploy,
a value saved there broke exactly one device while every other one worked. To point a *development*
build somewhere other than `localhost:8000`, set `VITE_API_BASE` in `frontend/.env.local`.

Do **not** add `frontend/public/_redirects` back if you're on Workers Builds — `wrangler.toml`'s
`not_found_handling = "single-page-application"` already does the SPA fallback, and having both
triggers Cloudflare's "infinite redirect loop" validator (error code 100324).

### Starting ngrok

If you passed `NGROK_AUTHTOKEN` to the bootstrap script, ngrok is installed and its authtoken is
configured — but its config lives on the ephemeral `/` overlay, so it needs re-adding after every pod
restart even though ngrok itself doesn't need reinstalling:

```bash
ngrok config add-authtoken <TOKEN>   # only needed again after a pod restart
tmux new-session -d -s ngrok "ngrok http --domain=<your-name>.ngrok-free.dev 8000"
```

### Generating and reusing secrets

The bootstrap script handles this: on its **first** run against a given `/workspace` it writes
`/workspace/vcs-secrets.env` (mode `600`) with a generated `VCS_API_KEY` and `VCS_MEDIA_TOKEN_SECRET`,
and every later run detects the file and reuses it rather than regenerating. It prints both values in
full at the end — save them somewhere durable (password manager, not just the terminal scrollback).

Load them on every restart with:

```bash
set -a; source /workspace/vcs-secrets.env; set +a
```

Regenerating instead of reusing has two separate consequences: a new `VCS_API_KEY` invalidates the key
every frontend user has saved in their browser, so they all start getting 401s with no visible cause;
a new `VCS_MEDIA_TOKEN_SECRET` invalidates every signed `?t=` audio URL already handed out, so audio
that was playing a moment ago starts 403ing. A **new** pod (fresh `/workspace`) has no secrets file and
gets a fresh one; treat the resulting key as new and tell anyone who had the old one.

### Verifying it

```bash
# Backend reachable and healthy (the ngrok header bypasses the free-tier interstitial —
# without it you'll see an HTML warning page instead of JSON)
curl -H "ngrok-skip-browser-warning: true" https://your-name.ngrok-free.dev/api/health

# CORS preflight succeeds for the deployed frontend's exact origin
curl -X OPTIONS -H "Origin: https://your-frontend-url" \
  -H "Access-Control-Request-Method: GET" \
  -H "ngrok-skip-browser-warning: true" \
  https://your-name.ngrok-free.dev/api/models
```

Then open the frontend URL, click the settings gear, paste the `VCS_API_KEY`, and save. That is the
only thing the gear asks for — the backend address is not configured in the browser at all.

### If audio won't play on a phone

Two separate causes, and they look identical from the outside — the play button does nothing while the
download button works.

1. **`Content-Disposition: attachment`.** Fixed: `/api/media/...` now serves `inline`, and the download
   button opts back in with `&download=1`. Mobile Safari and Android Chrome honour `attachment` on a
   media element and refuse to play; desktop Chrome and Firefox ignore it, which is why this survived
   until someone opened the app on a phone.

2. **The ngrok free-tier interstitial.** Fixed by the Worker proxy in `frontend/worker.js`; this is
   what it exists for. `api.ts` sets `ngrok-skip-browser-warning` on every `fetch`, but an `<audio>`
   element cannot send custom headers — the same reason media URLs are signed instead of
   key-authenticated. So the audio request came back as a 2.8 KB HTML warning page with
   `content-type: text/html` while the JSON API sailed through, and Safari reported
   `NotSupportedError` because the element never received audio at all.

   **Do not diagnose this by opening the API in the phone's browser** — that is a *first-party*
   navigation, so ngrok's cookie is sent and you get clean JSON, which looks like proof the
   interstitial is not your problem. It is not the same request. The `<audio>` element makes a
   *cross-site* request, and Safari's Intelligent Tracking Prevention blocks third-party cookies by
   default, so the cookie is never attached. That asymmetry is also the whole reason it presented as
   phone-only: desktop Chrome still sends the cookie cross-site, so the laptop worked throughout.

   Reproduce it correctly with curl instead — no skip header, browser UA:

   ```bash
   curl -sD - -o /dev/null -A "Mozilla/5.0 (iPhone...) Safari/604.1" \
     "https://<your-name>.ngrok-free.dev/api/media/voice/1?t=<token>"
   ```

   `content-type: text/html` means the interstitial; `audio/wav` means it is genuinely reaching the
   backend. Do not "fix" this by blob-fetching the audio — that forces a full download and breaks
   seeking.

**Visiting the ngrok URL itself in a browser is not the product** — it's a JSON API with no page at
`/` (that path 404s by design; `/docs` has the interactive Swagger UI if you want to poke at it
directly). The UI lives at the frontend URL, which calls the ngrok URL under the hood.

---

## YouTube transcript import needs cookies on a pod

YouTube blocks **datacenter / cloud IPs** — which every RunPod pod is — with *"Sign in to confirm
you're not a bot."* yt-dlp gets this on every player client, so transcript import fails with a 502
`TRANSCRIPT_FETCH_FAILED` even though the code and yt-dlp are fine. From a residential IP it works
without any of this; on a pod it needs a logged-in **cookies file**.

The backend reads `VCS_YT_COOKIES_FILE` (a Netscape `cookies.txt`) and hands it to yt-dlp. The
bootstrap's `serve.sh` already points it at **`/workspace/yt-cookies.txt`**, and a missing file is
ignored — so the pod starts fine without it and everything except import works. To enable import:

1. **On your own machine**, in a browser signed into a **throwaway** Google account (never your main
   one — cookie use from a datacenter IP can get an account flagged), export cookies for
   `youtube.com` in Netscape format. The [Get cookies.txt](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
   browser extensions do this; save it as `yt-cookies.txt`.
2. **Copy it to the pod** at the path `serve.sh` expects:

   ```bash
   scp -P <PORT> -i ~/.ssh/id_ed25519 yt-cookies.txt root@<HOST>:/workspace/yt-cookies.txt
   ```

3. **Restart the backend** so it picks up the file:

   ```bash
   ssh root@<HOST> -p <PORT> -i ~/.ssh/id_ed25519 "/workspace/ctl.sh restart"
   ```

Cookies **expire** — when import starts failing with the bot-block again, re-export and re-copy.
This is inherent to the approach, not a bug. The only alternative is routing the pod's traffic
through a residential proxy, which the app does not manage.

---

## Doing it by hand

The bootstrap script automates everything above. This section is a reference for when something in it
fails and you need to run the individual pieces yourself to see where — not a set of steps to follow
alongside the script under normal use.

**Caches**, so HF/torch/pip/uv don't fill the ephemeral `/` overlay:

```bash
export HF_HOME=/workspace/hf-cache TORCH_HOME=/workspace/torch-cache \
       PIP_CACHE_DIR=/workspace/pip-cache UV_CACHE_DIR=/workspace/uv-cache \
       TMPDIR=/workspace/tmp
mkdir -p /workspace/{hf-cache,torch-cache,pip-cache,uv-cache,tmp}
```

**`uv` and `ffmpeg`:**

```bash
command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="/root/.local/bin:$PATH"
command -v ffmpeg || (apt-get update -qq && apt-get install -y -qq ffmpeg)
```

**The repo** — public for read, no token needed:

```bash
git clone https://github.com/MunawarAliAraiz/AI-Voice-Clone.git /workspace/AI-Voice-Clone
```

If you need to push *from* the pod (rare — normally you develop locally and the pod only runs), a
token is needed for that direction only:

```bash
GH_USER=<you> GH_TOKEN=<token> git clone https://github.com/MunawarAliAraiz/AI-Voice-Clone.git /workspace/AI-Voice-Clone
```

**Backend API environment** (no torch, by design):

```bash
cd /workspace/AI-Voice-Clone/backend && uv sync --python 3.12
```

**VoxCPM 2 runtime environment** (this one has torch):

```bash
uv venv .venv-voxcpm --python 3.12
uv pip install --python .venv-voxcpm voxcpm
```

> **Critical torch pin.** A plain `voxcpm` install pulls a **torch cu130** wheel whose CUDA runtime is
> newer than the RunPod driver, so `torch.cuda.is_available()` is silently `False`, synthesis falls to
> CPU, and the request times out. Force the cu128 build the driver supports:

```bash
uv pip install --python .venv-voxcpm \
  torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
```

Verify — this **must** print `True`:

```bash
.venv-voxcpm/bin/python -c "import torch; print('cuda available:', torch.cuda.is_available())"
```

If your pod's driver is newer than CUDA 12.8, check `nvidia-smi` and bump the index URL (`cu129` /
`cu130`) to match the driver.

**VoxCPM 2 weights** (pinned revision, ~7 GB, cached on `/workspace`):

```bash
python3 - <<'PY'
from huggingface_hub import snapshot_download
p = snapshot_download("openbmb/VoxCPM2", revision="bffb3df5a29440629464e5e839f4d214c8714c3d")
print("weights at", p)
PY
```

---

## Notes

- **Native Urdu script (اردو) works — but you must pick the model by name.** Choose **OmniVoice
  (Urdu)** in the Composer's Model dropdown (it's pre-selected when the language is Urdu). Its
  `(ur, Perso-Arabic)` cell is `verified=True` as of 2026-08-15, and it was the best pronunciation
  result of the whole Urdu bake-off (5.0/5). Digits and a couple of English loanwords are normalized
  to spoken Urdu first — `2026` is read "دو ہزار چھبیس", not digit-by-digit.

  Leaving the picker on **Auto** still returns a 422 for Perso-Arabic Urdu, and that is deliberate,
  not a gap: OmniVoice's weights are CC-BY-NC, so `ModelCatalog.candidates()` excludes it from
  auto-routing and from fallback suggestions. Only an explicit, named request reaches a
  non-commercial model — see golden rule 6 in `CLAUDE.md`.

  Roman Urdu ("aap kaise hain") routes automatically to VoxCPM 2 and needs no picking. Roman Urdu
  → OmniVoice is **not** available: it would need a Roman→Perso-Arabic transliteration step, and four
  separate probes (two target scripts × two model sizes) all missed their accuracy gate. See
  `docs/URDU_BAKEOFF_RESULTS.md` §8–§8c.
