# Runpod setup from the desktop

## Simplified setup in 0.1.3

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
