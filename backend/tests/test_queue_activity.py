"""Real SQLite + API activity tests, isolated from GPU/cloud/normal profile."""
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.api.deps import get_db, get_scheduler, get_settings
from app.api.routers.jobs import router
from app.config import Settings
from app.db.database import Database
from app.inference.catalog import CATALOG
from app.inference.progress import emit_progress
from app.jobs import JobKind, JobRunner
from app.jobs.types import JobOutcome
from tests.fakes import FakeScheduler


async def test_old_active_and_watched_completion_survive_more_than_twenty_new_rows(tmp_path: Path):
    db = Database(tmp_path / 'queue.db')
    await db.connect()
    try:
        old = await db.create_job(kind='transliterate', params_json='{"texts":["old text"]}',
                                  route_json=None, profile_id=None)
        await db.claim_next_job('transliterate')
        for _ in range(25):
            row = await db.create_job(kind='analyze_llm', params_json='{}', route_json=None,
                                     profile_id=None)
            await db.finish_job(row['id'], history_id=None, result_json='{"rows":[]}')
        assert old['id'] not in [r['id'] for r in await db.list_jobs(limit=20)]
        assert old['id'] in [r['id'] for r in await db.list_activity_jobs([])]
        await db.finish_job(old['id'], history_id=None, result_json='{"items":[]}')
        assert old['id'] not in [r['id'] for r in await db.list_activity_jobs([])]
        assert old['id'] in [r['id'] for r in await db.list_activity_jobs([old['id']])]
    finally:
        await db.close()


async def test_activity_mixed_kinds_priority_position_and_cloud_unknown_eta(tmp_path: Path):
    db = Database(tmp_path / 'queue.db')
    await db.connect()
    try:
        first = await db.create_job(kind='transliterate', params_json='{"texts":["low"]}',
                                    route_json=None, profile_id=None, priority=0)
        high = await db.create_job(kind='transliterate', params_json='{"texts":["high"]}',
                                   route_json=None, profile_id=None, priority=5)
        helper = await db.create_job(kind='analyze_llm', params_json='{"sentences":["hello"]}',
                                     route_json=None, profile_id=None)
        app = FastAPI()
        app.include_router(router, prefix='/api')
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_settings] = lambda: Settings(remote_worker_url='https://test.invalid')
        app.dependency_overrides[get_scheduler] = lambda: FakeScheduler()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test',
        ) as c:
            response = await c.get('/api/jobs/activity')
            assert response.status_code == 200, response.text
            items = {r['id']: r for r in response.json()['items']}
            assert items[high['id']]['position'] == 0
            assert items[first['id']]['position'] == 1
            assert items[helper['id']]['position'] == 0  # separate kind, not global FIFO
            assert items[helper['id']]['input_text'] == 'hello'
            assert items[first['id']]['input_text'] == 'low'
            assert all(r['eta_sec'] is None and r['eta_state'] == 'unknown' for r in items.values())
            too_many = '/api/jobs/activity?' + '&'.join('tracked_id=1' for _ in range(257))
            assert (await c.get(too_many)).status_code == 422
    finally:
        await db.close()


async def test_runner_persists_real_model_stage_then_clears_terminal_phase(tmp_path: Path):
    db = Database(tmp_path / 'queue.db')
    await db.connect()
    try:
        row = await db.create_job(kind='analyze_llm', params_json='{}', route_json=None,
                                  profile_id=None)
        claimed = await db.claim_next_job('analyze_llm')
        from app.jobs.types import job_record_from_row
        job = job_record_from_row(claimed)
        runner = JobRunner(db, FakeScheduler(), CATALOG, Settings(data_dir=tmp_path))

        async def handler(ctx, current):
            await emit_progress('loading_model', 'qwen')
            stage = await db.get_job(current.id)
            assert stage['phase'] == 'loading_model'
            assert stage['phase_model_id'] == 'qwen'
            assert stage['phase_updated_at']
            await emit_progress('analyzing', 'qwen')
            assert (await db.get_job(current.id))['phase'] == 'analyzing'
            return JobOutcome(result={'rows': []})

        await runner._run_one(JobKind.ANALYZE_LLM, job, handler)
        final = await db.get_job(row['id'])
        assert final['status'] == 'succeeded'
        assert final['phase'] is None
        assert final['phase_model_id'] is None
    finally:
        await db.close()


async def test_offline_feed_benchmark_retains_coverage_and_bounds_calls(tmp_path: Path):
    import importlib.util
    import math
    path = Path(__file__).parents[2] / 'scripts' / 'benchmark-job-feed.py'
    spec = importlib.util.spec_from_file_location('queue_benchmark', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = await module.measure(tmp_path, iterations=3)
    before = report['results']['before_newest_twenty']
    after = report['results']['after_complete_activity']
    assert before['active_visible'] == 0
    assert after['active_visible'] == 35
    assert after['rows'] == 55
    assert after['sql_selects_max'] <= 2
    assert after['scheduler_status_calls_max'] == 1
    assert after['all_estimates_finite_positive']
    assert math.isfinite(after['median_ms']) and after['median_ms'] > 0
