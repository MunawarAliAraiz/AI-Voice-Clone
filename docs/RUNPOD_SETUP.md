# Runpod setup from the desktop

## Guided setup in 0.1.5 (awaiting installer qualification/publication)

1. **Account**: enter the Runpod key and connect. The app checks the account.
2. **Storage**: use the saved voice-app volume, or review a new volume's size
   and monthly price. The current minimum is 60 GB. Advanced lets you select
   an existing volume or choose a new volume's name, size and region. Creating
   new storage does not delete or stop billing for old storage.
3. **Models**: missing models download automatically after the storage and
   setup cost are confirmed. Each model shows its own progress. Pause/Resume
   controls are available. Leaving this page keeps downloads running.
4. **Ready**: approve generation limits. Once cloud files and local audio tools
   are ready, open Voice Studio. The first generation loads the GPU models.

Back/Next changes the page, not the running download. Closing the app pauses
setup and stops its temporary machine before exiting; reopening resumes eligible
setup. If Runpod cannot confirm the machine stopped, the app stays open with
Try again/Return to app. Failed or uncertain paid starts need attention rather
than silently starting another machine. See [guided setup](GUIDED_SETUP.md).

These source checks are complete; paid model transfer, GPU generation and the
installed close/reopen/update cycle have not been tested.

## Automatic setup in 0.1.4

The user-approved [0.1.4 release](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.4)
is published. Its anonymous feed and full installer match the approved bytes.
Apply it through **Updates** with local jobs and cloud setup
idle. If restart is blocked, open **Runpod** and select **Cancel model setup**;
wait for machine cleanup to be confirmed before restarting. Ambiguous starts
still need reconciliation and cannot be bypassed.

1. Connect Runpod and refresh storage/funds. Suitable existing 200 GB Standard
   storage is selected by default; otherwise review and approve its purchase.
2. Required model preparation starts automatically once storage is connected.
   The app uses the dedicated CPU deployment method and selected CPU instance;
   it does not require generation-GPU stock to download weights. Temporary
   setup has a $1 cap and at most a two-hour requested termination deadline.
3. Follow **Set up models**: start machine, named model downloads, file checks,
   cleanup, then ready. Percentages require measured bytes. Valid existing
   files are reused; incomplete files resume when their download source supports it.
4. **Cancel model setup** keeps storage and model files and turns automatic
   setup off. **Resume automatic downloads** re-enables it. Availability-only
   retries are bounded; a failed paid or uncertain attempt needs explicit resume.
5. Approve the displayed generation spending limits. On generation, the app
   chooses a compatible available GPU within those limits and releases it after
   the queue finishes. Advanced overrides remain collapsed.

**Cancel download** in Updates pauses only the installer transfer. In 0.1.4,
verified installers survive closing the app or a blocked restart. Partial
installers resume only if the server confirms the same file and byte range;
otherwise the app safely downloads a fresh copy. Files lost by closing an older
memory-only updater cannot be restored retroactively.

The request correction and offline checks passed. A real paid CPU deployment,
model download, GPU generation and installed update/restart remain unverified.

## Historical simplified setup in 0.1.3

The published [0.1.3 update](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.3)
uses three cards: **Model storage**, **Set up models**, and
**Voice generation**. Advanced settings are collapsed. The app chooses the
cheapest compatible available GPU; saving spending limits does not start it.
The default limits are visible and require a click to approve. Custom values
stay in Advanced settings and reflect previously saved limits.

Model setup separately identifies starting the download machine/software,
checking existing storage, downloading named models, verifying files, and
stopping the machine. Download percentages appear only with actual byte totals.
A failure stops progress and shows the next action. An unconfirmed start is
not presented as a billed rental or as downloaded models. Use **Check pending
start** to reconcile; an uncertain attempt cannot be retried until cleared or
its requested deadline has passed. This avoids duplicate machine creation.

The Voice Studio model override is also collapsed. Existing recommended model
choices remain; noncommercial/experimental warnings remain visible. Explicit
Auto is still available, but unsupported Urdu Arabic requires an informed
model choice rather than silently selecting a different model.

Apply it through **Updates** when local jobs and Runpod sessions are idle.
If restart reports a current cloud session, reconcile/release the pending
session first; the updater does not bypass an ambiguous creation record.
The clearer progress display does not establish that the earlier provider
deployment error has been resolved. Paid downloads/generation remain untested.

## Applying the worker-enabled release

Desktop 0.1.1 does not include a verified cloud worker release. Connecting the
API, checking funds and reviewing storage quotes work, but purchasing storage
and downloading models remain disabled. Do not create a manual GPU Pod to
work around this; the app needs its own authenticated worker and model evidence.

Both worker image jobs in
[36964451430](https://github.com/MunawarAliAraiz/AI-Voice-Clone/actions/runs/36964451430)
succeeded for source `e27c1ca87e2d56608c74f50b60bc394b0ddf4ade`. Their matching
immutable images passed anonymous registry verification and are now included
in the release manifest. Desktop 0.1.2 is built and verified with this manifest
and the account-card width fix. It is published at
[v0.1.2](https://github.com/MunawarAliAraiz/AI-Voice-Clone/releases/tag/v0.1.2);
anonymous latest-feed and full installer downloads match the verified files.
The running 0.1.1 cannot gain these references by
connecting its API key. Actual GPU generation quality remains untested.

## Setup after applying 0.1.2

1. Open **Updates**, check, download, then restart/apply when your jobs are idle.
2. Open **Runpod** and connect the API key if needed. Select **Refresh storage
   and funds**. An API connection alone does not mean the models are ready.
3. Select a suitable existing Standard volume of at least 200 GB. Otherwise,
   choose a storage region and select **Review storage purchase**.
4. Review recurring storage price and the available-credit/setup reserve.
   Select **Purchase storage and set up models**. The quote expires, so obtain
   a fresh quote after adding funds or if the button reports expiration.
5. Wait for model download and checksum progress to finish. Existing valid
   weights are adopted; missing files download on a temporary setup worker.
   Current pinned files total about 49 GB. Storage remains after compute ends.
6. Set and approve your maximum session spend and GPU hourly rate under
   **Automatic generation compute**. The app chooses available compatible
   compute in the storage region within those limits.
7. Add an authorized reference voice, enter your script and generate. Follow
   the job in **Recent**. Generation is admitted only after local audio tools,
   model evidence and compute limits are ready.

Storage is billed while retained. Startup/model loading are billed compute
time too. The managed mode currently uses a terminated Pod with a five-second
queue grace; Serverless comparison remains unfinished. Use current app quotes
for costs rather than treating an example screenshot as a permanent rate.

If a button is disabled, read the nearby reason or focus/hover its explanation.
For a worker-release blocker, check the next desktop update. For insufficient
funds, add credit and refresh; for unavailable compute, refresh stock or review
the rate limit. API/status reads do not create a Pod.
