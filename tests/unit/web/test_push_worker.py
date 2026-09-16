import subprocess
from pathlib import Path


def test_worker_payload_navigation_and_no_api_cache() -> None:
    script = Path("src/matoca_service/web/static/sw.js").resolve()
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            """
      import fs from 'node:fs'; import vm from 'node:vm'; import assert from 'node:assert/strict';
      const events = {}, shown = [], opened = [];
      const self = {location:{origin:'https://queue.example.test'},
        addEventListener:(name, callback) => events[name] = callback,
        registration:{showNotification:async (...args) => shown.push(args)},
        clients:{matchAll:async () => [], openWindow:async url => opened.push(url)}};
      vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {self, URL});
      assert.equal(events.fetch, undefined);
      for (const target of ['https://evil.test/', '//evil.test/', '/api/push/public-key',
          'https://user@queue.example.test/', '/merchants/sawayaka']) {
        let pending;
        events.push({data:{json:() => ({title:'お知らせ', body:'確認してください', url:target})},
          waitUntil:p => pending=p}); await pending;
        const [, options] = shown.at(-1);
        const expected = target === '/merchants/sawayaka'
          ? 'https://queue.example.test/merchants/sawayaka' : 'https://queue.example.test/';
        assert.equal(options.data.url, expected);
        events.notificationclick({notification:{data:{url:target},close:()=>{}},
          waitUntil:p => pending=p}); await pending;
        assert.equal(opened.at(-1), expected);
      }
      let pending;
      events.push({data:{json:()=>{throw Error('invalid')}}, waitUntil:p => pending=p});
      await pending; assert.equal(shown.at(-1)[0], '順番待ちのお知らせ');
      console.log('worker behavior passed');
    """,
            str(script),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
