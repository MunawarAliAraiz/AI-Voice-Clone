"""
Test doubles.

This package is the ONLY place a fake audio path may exist. The mock engine was
deleted from the product because it shipped a 440 Hz sine wave with HTTP 200
whenever no real engine was loaded, and every layer above it was untrustworthy
as a result.

If a no-GPU demo is ever genuinely needed, the rule from the plan stands:
`FakeRuntime` behind `VCS_ALLOW_FAKE_RUNTIME=1`, a loud banner in the UI, and an
`X-Fake-Audio: true` response header. Never a silent fallback, never a default,
and never reachable from a normal request path.

Note `FakeScheduler.synthesize` writes the literal bytes `FAKE-NOT-AUDIO` rather
than a playable tone — on purpose. A fake that produces something audible is a
fake that eventually gets mistaken for output.
"""

from .scheduler import FakeScheduler
from .worker import FakeWorker, FakeWorkerCrash

__all__ = ["FakeScheduler", "FakeWorker", "FakeWorkerCrash"]
