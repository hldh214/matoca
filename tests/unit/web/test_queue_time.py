import subprocess
from pathlib import Path


def test_official_call_time_uses_observation_not_page_clock() -> None:
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            """
import assert from 'node:assert/strict';
import {callTime} from './src/matoca_service/web/static/queue-time.js';
const observation = {observed_at: '2026-09-23T05:00:00Z', official_minutes: 30,
  official_is_more: false, raw_status: 2};
const item = {status: 'active', observations: [observation]};
const now = new Date('2026-09-23T05:01:00Z');
assert.equal(callTime(item, {now, timezone: 'Asia/Tokyo'}), '14:30ごろ');
assert.equal(callTime(item, {now: new Date('2026-09-23T05:02:00Z'),
  timezone: 'Asia/Tokyo'}), '14:30ごろ');
assert.equal(callTime(item, {now, timezone: 'America/Los_Angeles'}), '22:30ごろ');
assert.equal(callTime(item, {now, timezone: 'invalid'}), '14:30ごろ');
assert.equal(callTime({...item, stale: true}, {now}), '更新待ち');
assert.equal(callTime(item, {now: new Date('2026-09-23T05:05:00Z')}), '更新待ち');
for (const [status, expected] of [
  [8, 'まもなく呼び出し'], [4, '呼出中'], [5, '保留中'], [6, '完了']]) {
  assert.equal(callTime({ ...item,
  observations: [{...observation, raw_status: status}]}, {now}),
    expected);
}
assert.equal(callTime({ ...item,
  observations: [{...observation, official_is_more: true}]},
  {now, timezone: 'Asia/Tokyo'}), '14:30以降');
assert.equal(callTime({ ...item,
  observations: [{...observation, official_minutes: null}]}, {now}), '目安なし');
assert.equal(callTime({ ...item,
  observations: [{...observation, official_minutes: 0}]}, {now}), '公式目安を経過');
assert.equal(callTime({ ...item,
  observations: [{...observation, observed_at: '2026-09-23T14:50:00Z'}]},
  {now: new Date('2026-09-23T14:51:00Z'), timezone: 'Asia/Tokyo'}), '9/24 00:20ごろ');
""",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
