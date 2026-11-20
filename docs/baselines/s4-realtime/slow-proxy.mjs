// 仅供本机验收。收到上游响应头后按标记延迟转发，保留真实推理结果。
import http from 'node:http';
import { existsSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const marker = process.argv[2];
if (!marker) throw Error('需要延迟标记文件路径');
http.createServer((req, res) => {
  const upstream = http.request({
    hostname: '127.0.0.1', port: 18764, path: req.url,
    method: req.method, headers: req.headers,
  }, async response => {
    if (req.method === 'POST' && existsSync(marker)) {
      await sleep(2000);
      console.log(JSON.stringify({ path: req.url, delay_ms: 2000, at: Date.now() }));
    }
    res.writeHead(response.statusCode, response.headers);
    response.pipe(res);
  });
  upstream.on('error', () => { res.writeHead(502); res.end(); });
  req.pipe(upstream);
}).listen(18767, '127.0.0.1');
