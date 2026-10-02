# CPU model setup request investigation

Observed on 2026-10-02. This is schema and catalog evidence, not a successful
paid worker deployment.

## What was wrong with the previous request

The app quoted a CPU download machine from the REST v2 CPU catalog, but discarded
the selected CPU flavor and its instance configuration. Its creation request
always called `podFindAndDeployOnDemand` with
`PodFindAndDeployOnDemandInput!`, including `computeType: CPU`, `gpuCount: 0`,
`minVcpuCount: 2` and `minMemoryInGb: 4` for an installer.

The official GraphQL SDK [v1.7.13 deployment generator](https://github.com/runpod/runpod-python/blob/v1.7.13/runpod/api/mutations/pods.py)
uses a separate `deployCpuPod` mutation for CPU Pods and an `instanceId` such as
`cpu3c-2-4`. It includes minimum RAM/vCPU and GPU count only in the GPU branch.
The [SDK controller](https://github.com/runpod/runpod-python/blob/v1.7.13/runpod/api/ctl_commands.py)
also reads the CPU result from `data.deployCpuPod`.

The [current REST SDK](https://github.com/runpod/runpod-python/blob/main/runpod/api/ctl_commands.py)
requires a CPU instance selector with the format
`<cpu-flavor>-<vcpu-count>-<memory>`. Its newer REST route does not establish an
atomic provider termination deadline, so switching to an unguarded REST CPU
creation request would not meet this app's billing protection requirement.

The public [Runpod GraphQL specification](https://graphql-spec.runpod.io/)
lists a CPU enum and `instanceIds` on the generic deployment input but omits the
dedicated CPU mutation. This documentation alone did not prove that the CPU
mutation accepts the required deadline. The old generic error response also
did not retain the original resolver reason, so it is not possible to prove
which individual field caused that particular failed request.

## Safe validation against the live schema

Introspection is disabled. We instead sent deliberately invalid GraphQL
documents with the following mandatory root field:

```graphql
__vcs_never_execute_validation_probe__
```

This reserved, nonexistent root field makes the entire operation invalid.
[GraphQL validation](https://spec.graphql.org/September2025/#sec-Validation)
must finish successfully before operation execution. Therefore no deployment
resolver could run. No real volume ID, worker token, account ID or runnable
image was used in the documents.

The inline validation probe was:

```graphql
mutation ValidationOnly {
  __vcs_never_execute_validation_probe__
  deployCpuPod(input: {
    name: "validation-only"
    imageName: "invalid.example/never-execute:invalid"
    cloudType: SECURE
    instanceId: "cpu3c-2-4"
    containerDiskInGb: 10
    volumeMountPath: "/workspace"
    networkVolumeId: "not-a-real-volume"
    dataCenterId: "US-NE-1"
    ports: "8000/http"
    terminateAfter: "2099-01-01T00:00:00Z"
    startSsh: false
    startJupyter: false
    env: []
  }) {
    id
    costPerHr
  }
}
```

Runpod returned HTTP 400 with `GRAPHQL_VALIDATION_FAILED`, no `data`, and only
the error for the mandatory unknown root field. This confirms that the listed
CPU input fields, including `imageName`, `networkVolumeId` and `terminateAfter`,
and both selected output fields are accepted by the current schema.

A second invalid probe using a string instead of the CPU input object reported
the exact input type as `deployCpuPodInput!`, with a lowercase initial `d`.
The candidate `DeployCpuPodInput` with an uppercase `D` was explicitly rejected
as an unknown type. Both probes also confirmed the mandatory unknown root
field rejection and contained no returned data.

Use the dedicated CPU mutation and exact case-sensitive input type in the
installer path, retain the provider deadline in the same creation input, and
pass the concrete catalog instance selector that matches the quoted price.

## Live catalog evidence beside existing storage

Read-only `GET /v2/catalog/cpus?include=AVAILABILITY&product=POD` returned HTTP
200. For the existing storage region `US-NE-1`:

| CPU flavor | vCPU range | GB RAM per vCPU | Price per vCPU/hour | Regional stock | Minimum suitable instance | Total/hour |
| --- | --- | --- | --- | --- | --- | --- |
| `cpu3c` | 2–32 | 2 | $0.03 | HIGH | `cpu3c-2-4` | $0.06 |
| `cpu3g` | 2–32 | 4 | $0.04 | HIGH | `cpu3g-2-8` | $0.08 |
| `cpu3m` | 2–32 | 8 | $0.055 | HIGH | `cpu3m-2-16` | $0.11 |

The `cpu5c`, `cpu5g` and `cpu5m` entries had no stock entry for this region.
Availability and rates can change before deployment; fetch the catalog again
when starting setup. These are CPU download-worker prices, not GPU generation
prices. The cheapest observed instance provides the required two vCPUs and
4 GB RAM.

## Qualification limits and next check

- Schema validation proves supported input/output fields, not allocation,
  container image startup, mounting, proxy access or execution.
- The schema accepting `terminateAfter` does not independently prove the
  provider enforces termination. A real bounded deployment must confirm its
  scheduled termination and eventual removal before declaring the billing
  guard qualified.
- No CPU or GPU machine was created by this investigation, and no model files
  were downloaded or changed.
- Do not silently replay an earlier ambiguous creation attempt. Reconcile its
  unique machine name and pending record before allowing another paid start.
- The next live check should use the corrected dedicated CPU request with the
  actual selected volume, immutable installer image, two-vCPU instance,
  short bounded deadline and verified cleanup.

Local read-only helpers are kept in the ignored `build/` directory of the
primary checkout: `validate-runpod-cpu-input-readonly.py` and
`inspect-runpod-cpu-catalog-readonly.py`. They never print credentials.
