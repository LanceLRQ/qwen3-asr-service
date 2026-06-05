/* 实时转写页（Vue 3 + Naive UI，无构建 UMD）。
 * 协议：WS /v2/asr/stream（?token= 鉴权），start/binary/stop 信封；
 * 麦克风经 AudioWorklet（/web-ui/assets/pcm-worklet.js）采集，主线程重采样 16k Int16；
 * 文件模拟推流优先 ffmpeg-wasm（CDN 懒加载，失败回退浏览器原生解码），200ms 分帧 + 1MB 背压。
 */
(function () {
  'use strict';
  const { ref, reactive, computed, watch, onMounted, onBeforeUnmount } = Vue;
  const { fmtMs, fmtBytes, spkIdx, apiKey, mountApp } = window.AsrCommon;

  const RT_SR = 16000;
  const FRAME = 3200;                 // 200ms @16k
  const BP_LIMIT = 1 << 20;           // 1MB 发送缓冲上限（背压）
  const MAX_LOG_LINES = 300;
  const MAX_TRANSCRIPT_LINES = 200;
  const FFMPEG_MIRROR = 'https://unpkg.zhimg.com';
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  function floatToInt16(f32) {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const v = Math.max(-1, Math.min(1, f32[i]));
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    return out;
  }
  // 麦克风：任意采样率线性重采样到 16k 并转 Int16
  function micFloatTo16kPCM(input, inputSr) {
    if (inputSr === RT_SR) return floatToInt16(input);
    const ratio = inputSr / RT_SR, outLen = Math.floor(input.length / ratio);
    const out = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const idx = i * ratio, i0 = Math.floor(idx), frac = idx - i0;
      const s = (input[i0] || 0) * (1 - frac) + (input[i0 + 1] || 0) * frac;
      const v = Math.max(-1, Math.min(1, s));
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    }
    return out;
  }

  const AppBody = {
    setup() {
      const lang = ref('');
      // —— 声纹识别（随 start 消息发送；capabilities 探测到 speaker_identification 才显示开关）——
      const canIdentify = ref(false);
      const identifySpeakers = ref(false);

      // —— 会话状态机：idle | connecting | streaming | stopping ——
      const streamState = ref('idle');
      const statusText = ref('未连接');
      const source = ref('mic');          // mic | file
      const busy = computed(() => streamState.value !== 'idle');

      // —— 提示与能力 ——
      const capWarning = ref('');
      const hint = ref('');
      const capInfo = ref('');

      // —— 结果 ——
      const finals = reactive([]);        // {key, start, text, words, speaker, speakerName}
      const partial = ref('');
      let finalSeq = 0;
      function appendFinal(m) {
        finals.push({ key: ++finalSeq, start: m.start, text: m.text || '', words: (m.words && m.words.length) || 0, speaker: m.speaker || null, speakerName: m.speaker_name || null });
        if (finals.length > MAX_TRANSCRIPT_LINES) finals.shift();
      }
      function clearResults() { finals.length = 0; partial.value = ''; }

      // 满高布局下转写区内部滚动：新 final/partial 到达时跟随滚底
      const transcriptRef = ref(null);
      watch([() => finals.length, partial], () => {
        Vue.nextTick(() => { const el = transcriptRef.value; if (el) el.scrollTop = el.scrollHeight; });
      });

      // —— 协议日志 ——
      const logs = reactive([]);          // {key, ts, kind, text}
      const logOpen = ref(false);
      let logSeq = 0;
      const logRef = ref(null);
      function log(kind, data) {
        const ts = new Date().toISOString().substr(11, 12);
        const body = typeof data === 'string' ? data : JSON.stringify(data);
        logs.push({ key: ++logSeq, ts, kind, text: body });
        if (logs.length > MAX_LOG_LINES) logs.shift();
        Vue.nextTick(() => { const el = logRef.value; if (el) el.scrollTop = el.scrollHeight; });
      }

      // —— 诊断指标（n-statistic 横排：发送/接收速率、WS 缓冲、最大帧、主线程卡顿）——
      const diag = reactive({ on: false, sent: 0, recv: 0, buf: 0, frame: 0, stall: 0 });
      let diagTimer = null, rafId = null, sentFrames = 0, recvMsgs = 0, lastRaf = 0, maxStall = 0, maxFrame = 0;
      function startDiag() {
        sentFrames = 0; recvMsgs = 0; maxStall = 0; lastRaf = 0; maxFrame = 0;
        diag.on = true;
        const tick = now => {
          if (lastRaf) { const gap = now - lastRaf; if (gap > maxStall) maxStall = gap; }
          lastRaf = now;
          rafId = requestAnimationFrame(tick);
        };
        rafId = requestAnimationFrame(tick);
        diagTimer = setInterval(() => {
          diag.sent = sentFrames; diag.recv = recvMsgs;
          diag.buf = Math.round((ws ? ws.bufferedAmount : 0) / 1024);
          diag.frame = maxFrame; diag.stall = Math.round(maxStall);
          sentFrames = 0; recvMsgs = 0; maxStall = 0; maxFrame = 0;
        }, 1000);
      }
      function stopDiag() {
        if (diagTimer) { clearInterval(diagTimer); diagTimer = null; }
        if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
      }

      // —— VU 电平表（麦克风 RMS 包络，rAF 绘制 + 指数衰减）——
      const vuRef = ref(null);
      let vuRaf = null, vuLevel = 0;
      function drawVu() {
        const cv = vuRef.value;
        if (cv) {
          const ctx = cv.getContext('2d'), w = cv.width, hgt = cv.height;
          ctx.clearRect(0, 0, w, hgt);
          const lvl = Math.min(1, vuLevel * 4);
          if (lvl > 0.004) {
            const grd = ctx.createLinearGradient(0, 0, w, 0);
            grd.addColorStop(0, '#14b8a6'); grd.addColorStop(0.72, '#f59e0b'); grd.addColorStop(1, '#ef4444');
            ctx.fillStyle = grd;
            ctx.fillRect(0, 0, w * lvl, hgt);
          }
          vuLevel *= 0.86;
        }
        vuRaf = requestAnimationFrame(drawVu);
      }
      function startVu() { if (!vuRaf) drawVu(); }
      function stopVu() {
        if (vuRaf) { cancelAnimationFrame(vuRaf); vuRaf = null; }
        vuLevel = 0;
        const cv = vuRef.value;
        if (cv) cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
      }

      // —— WebSocket ——
      let ws = null;
      function wsSendJson(obj) {
        if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); log('send', obj); }
      }
      function startMsg() {
        const l = lang.value.trim();
        const m = { type: 'start', audio_fs: RT_SR, wav_name: 'web-test' };
        if (l && l !== 'auto') m.language = l;
        if (identifySpeakers.value) m.identify_speakers = true;
        return m;
      }
      function waitDrain() {
        return new Promise(res => {
          if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount < BP_LIMIT) return res();
          const iv = setInterval(() => {
            if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount < BP_LIMIT) { clearInterval(iv); res(); }
          }, 20);
        });
      }
      function openWs(onReady) {
        hint.value = '';
        clearResults();
        pushedBytes = 0; pushStartTs = 0; procEndMs = 0;   // 流控状态按会话重置
        const t = apiKey.value.trim();
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        const url = proto + '://' + location.host + '/v2/asr/stream' + (t ? '?token=' + encodeURIComponent(t) : '');
        streamState.value = 'connecting';
        statusText.value = '连接中…';
        ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => log('evt', 'WS open');
        ws.onmessage = ev => {
          recvMsgs++;
          let m;
          try { m = JSON.parse(ev.data); } catch (e) { return; }
          log('recv', m);
          if (m.type === 'session.created') {
            if (m.limits && m.limits.max_backlog_bytes) {
              backlogBudget = Math.floor(m.limits.max_backlog_bytes * 0.75);
            }
            capInfo.value = '协议 ' + m.protocol + ' v' + m.protocol_version + ' · 模式 ' + m.mode +
              ' · 后端 ' + m.backend + ' · 采样率 ' + m.sample_rate + ' · 能力 ' + JSON.stringify(m.capabilities);
            statusText.value = '已连接 · ' + m.backend;
            streamState.value = 'streaming';
            if (onReady) onReady();
          } else if (m.type === 'partial') {
            partial.value = m.text || '';
          } else if (m.type === 'final') {
            if (m.end != null && m.end > procEndMs) procEndMs = m.end;   // 服务端处理进度反馈
            appendFinal(m);
            partial.value = '';
          } else if (m.type === 'error') {
            hint.value = '[' + m.code + '] ' + m.message;
          } else if (m.type === 'session.closed') {
            statusText.value = '会话结束';
          }
        };
        ws.onerror = () => log('evt', 'WS error');
        ws.onclose = ev => {
          log('evt', 'WS close code=' + ev.code);
          statusText.value = '已断开 (' + ev.code + ')';
          if (ev.code === 1008) hint.value = '鉴权失败：请检查 API Key。';
          else if (ev.code === 1013) hint.value = '并发会话已满（1013）。';
          else if (ev.code === 1011) hint.value = '实时端点未就绪：请用 --serve-mode standard --enable-stream 启动服务。';
          streamState.value = 'idle';
          cleanupMic();
        };
      }
      function closeWs() { try { if (ws) ws.close(); } catch (e) { /* 已断开 */ } }
      // stopping 阶段的逃生口：不等服务端排空末段，立即断开复位（服务端挂起时界面不至于锁死）
      function forceClose() {
        fileAborted = true;
        closeWs();
        cleanupMic();
        streamState.value = 'idle';
        statusText.value = '已断开';
      }

      // —— 麦克风（AudioWorklet 外置文件）——
      let micCtx = null, micNode = null, micSrc = null, micStream = null;
      async function startMic() {
        hint.value = '';
        try {
          micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e) {
          hint.value = '麦克风访问失败: ' + e.message;
          return;
        }
        openWs(async () => {
          wsSendJson(startMsg());
          // 直接建 16kHz 上下文：免主线程重采样、降低 worklet 投递频率；不支持则回退默认采样率
          const AC = window.AudioContext || window.webkitAudioContext;
          try { micCtx = new AC({ sampleRate: RT_SR }); } catch (e) { micCtx = new AC(); }
          try {
            await micCtx.audioWorklet.addModule('/web-ui/assets/pcm-worklet.js');
          } catch (e) {
            hint.value = 'AudioWorklet 加载失败: ' + e.message;
            cleanupMic(); closeWs();
            return;
          }
          micSrc = micCtx.createMediaStreamSource(micStream);
          micNode = new AudioWorkletNode(micCtx, 'pcm-worklet');
          micNode.port.onmessage = ev => {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            const d = ev.data;
            if (d.length > maxFrame) maxFrame = d.length;
            // VU：抽样计算 RMS 包络（1/4 采样足够）
            let s = 0;
            for (let i = 0; i < d.length; i += 4) s += d[i] * d[i];
            const rms = Math.sqrt(s / Math.ceil(d.length / 4));
            if (rms > vuLevel) vuLevel = rms;
            if (ws.bufferedAmount > BP_LIMIT) return;   // 背压丢帧
            ws.send(micFloatTo16kPCM(d, micCtx.sampleRate).buffer);
            sentFrames++;
          };
          // worklet 不写 output → 输出静音；连 destination 仅为驱动音频图
          micSrc.connect(micNode);
          micNode.connect(micCtx.destination);
          startDiag();
          startVu();
          statusText.value = '录音中…';
        });
      }
      function stopMic() {
        streamState.value = 'stopping';
        wsSendJson({ type: 'stop' });
        cleanupMic();
        statusText.value = '已停止，等待末段结果…';
      }
      function cleanupMic() {
        stopDiag();
        stopVu();
        try { if (micNode) { micNode.port.onmessage = null; micNode.disconnect(); micNode = null; } } catch (e) { /* noop */ }
        try { if (micSrc) { micSrc.disconnect(); micSrc = null; } } catch (e) { /* noop */ }
        try { if (micCtx) { micCtx.close(); micCtx = null; } } catch (e) { /* noop */ }
        try { if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; } } catch (e) { /* noop */ }
      }

      // —— ffmpeg-wasm 懒加载（外网 CDN；失败回退浏览器原生解码）——
      let ffmpeg = null, ffmpegTried = false;
      function loadScript(src) {
        return new Promise((res, rej) => {
          const s = document.createElement('script');
          s.src = src; s.async = true;
          s.onload = res;
          s.onerror = () => rej(new Error('脚本加载失败: ' + src));
          document.head.appendChild(s);
        });
      }
      async function getFFmpeg() {
        if (ffmpeg) return ffmpeg;
        if (ffmpegTried) return null;
        ffmpegTried = true;
        try {
          statusText.value = '正在加载转码器 (ffmpeg-wasm)…';
          if (!window.FFmpegWASM) await loadScript(FFMPEG_MIRROR + '/@ffmpeg/ffmpeg@0.12.10/dist/umd/ffmpeg.js');
          if (!window.FFmpegUtil) await loadScript(FFMPEG_MIRROR + '/@ffmpeg/util@0.12.1/dist/umd/index.js');
          const { FFmpeg } = window.FFmpegWASM;
          const { toBlobURL } = window.FFmpegUtil;
          const core = FFMPEG_MIRROR + '/@ffmpeg/core@0.12.10/dist/umd';
          const ff = new FFmpeg();
          ff.on('log', ({ message }) => log('evt', 'ffmpeg: ' + message));
          await ff.load({
            coreURL: await toBlobURL(core + '/ffmpeg-core.js', 'text/javascript'),
            wasmURL: await toBlobURL(core + '/ffmpeg-core.wasm', 'application/wasm'),
          });
          ffmpeg = ff;
          log('evt', 'ffmpeg-wasm 已就绪');
          return ff;
        } catch (e) {
          log('evt', 'ffmpeg-wasm 加载失败，回退浏览器原生解码: ' + e.message);
          return null;
        }
      }
      // 文件 → 16k 单声道 PCM16。优先 ffmpeg-wasm（直出 s16le），失败回退 Web Audio
      async function decodeFileTo16kPCM(file) {
        const ff = await getFFmpeg();
        if (ff) {
          const { fetchFile } = window.FFmpegUtil;
          await ff.writeFile('input', await fetchFile(file));
          await ff.exec(['-i', 'input', '-ac', '1', '-ar', String(RT_SR), '-f', 's16le', 'out.pcm']);
          const out = await ff.readFile('out.pcm');
          try { await ff.deleteFile('input'); await ff.deleteFile('out.pcm'); } catch (e) { /* noop */ }
          return new Int16Array(out.buffer, out.byteOffset, Math.floor(out.byteLength / 2));
        }
        const buf = await file.arrayBuffer();
        const ac = new (window.AudioContext || window.webkitAudioContext)();
        const decoded = await ac.decodeAudioData(buf);
        ac.close();
        const dstLen = Math.ceil(decoded.length * RT_SR / decoded.sampleRate);
        const off = new OfflineAudioContext(1, dstLen, RT_SR);
        const bs = off.createBufferSource();
        bs.buffer = decoded; bs.connect(off.destination); bs.start();
        const rendered = await off.startRendering();
        return floatToInt16(rendered.getChannelData(0));
      }

      // —— 文件模拟推流 ——
      const streamFile = ref(null);
      const streamFileList = ref([]);
      const noThrottle = ref(false);
      const fileProgress = ref(0);
      const fileRunning = ref(false);
      let fileAborted = false;
      function onStreamUploadChange(payload) {
        const list = payload.fileList;
        const item = list.length ? list[list.length - 1] : null;
        streamFileList.value = item ? [item] : [];
        streamFile.value = item && item.file ? item.file : null;
        fileProgress.value = 0;
      }
      const streamFileSize = computed(() => (streamFile.value ? fmtBytes(streamFile.value.size) : ''));

      // —— 不限速流控：贴着服务端积压上限推，而非无脑全速（避免 backlog_overflow 断连）——
      // 预算 = session.created 下发的 max_backlog_bytes × 75%（留 25% 余量）；
      // 估算服务端未处理积压 = 已发字节 − max(实时消耗 32000B/s, final.end 反馈的已处理进度)，
      // 服务端处理快（GPU）则 final 反馈快、预算回填快，自动逼近其最大吞吐。
      // 服务端恒在 session.created 下发 limits；缺失时用保守小预算兜底（不镜像服务端默认值，避免两端漂移）
      const FALLBACK_BACKLOG_BYTES = 1024 * 1024;
      const BYTES_PER_SEC = RT_SR * 2;            // PCM16 单声道字节率
      let backlogBudget = Math.floor(FALLBACK_BACKLOG_BYTES * 0.75);
      let pushedBytes = 0, pushStartTs = 0, procEndMs = 0;
      function estBacklog() {
        const byTime = pushStartTs ? (performance.now() - pushStartTs) / 1000 * BYTES_PER_SEC : 0;
        const byFinal = procEndMs / 1000 * BYTES_PER_SEC;
        return pushedBytes - Math.max(byTime, byFinal);
      }
      async function waitBacklogBudget() {
        while (!fileAborted && ws && ws.readyState === WebSocket.OPEN &&
               estBacklog() + FRAME * 2 > backlogBudget) await sleep(50);
      }
      async function startFile() {
        hint.value = '';
        const file = streamFile.value;
        if (!file) { hint.value = '请先选择音频文件。'; return; }
        fileRunning.value = true;
        fileProgress.value = 0;
        fileAborted = false;

        let pcm16;
        try {
          pcm16 = await decodeFileTo16kPCM(file);
        } catch (e) {
          hint.value = '音频解码失败: ' + e.message;
          fileRunning.value = false;
          statusText.value = '未连接';
          return;
        }
        if (fileAborted) { fileRunning.value = false; return; }

        openWs(async () => {
          wsSendJson(startMsg());
          statusText.value = '推流中…';
          startDiag();
          pushStartTs = performance.now();
          const total = pcm16.length;
          const frameMs = FRAME / RT_SR * 1000;   // 200ms
          for (let i = 0; i < total; i += FRAME) {
            if (fileAborted || !ws || ws.readyState !== WebSocket.OPEN) { fileRunning.value = false; return; }
            await waitDrain();                     // 背压：发送缓冲过高时等排空
            if (noThrottle.value) await waitBacklogBudget();   // 不限速＝贴服务端积压上限控速
            if (fileAborted || !ws || ws.readyState !== WebSocket.OPEN) { fileRunning.value = false; return; }
            const chunk = pcm16.subarray(i, Math.min(i + FRAME, total));
            ws.send(chunk);
            pushedBytes += chunk.byteLength;
            sentFrames++;
            fileProgress.value = Math.round(Math.min(i + FRAME, total) / total * 100);
            if (!noThrottle.value) await sleep(frameMs);
          }
          if (fileAborted || !ws || ws.readyState !== WebSocket.OPEN) { fileRunning.value = false; return; }
          fileProgress.value = 100;
          streamState.value = 'stopping';
          wsSendJson({ type: 'stop' });
          statusText.value = '推流完成，等待末段结果…';
          fileRunning.value = false;
        });
      }
      function stopFile() {
        fileAborted = true;
        streamState.value = 'stopping';
        wsSendJson({ type: 'stop' });
        statusText.value = '已停止';
        fileRunning.value = false;
      }

      // —— 能力预检 ——
      async function precheck() {
        try {
          const r = await fetch('/v2/capabilities');
          if (!r.ok) return;
          const c = await r.json();
          canIdentify.value = !!c.speaker_identification;
          if (!c.stream || !c.stream.enabled) {
            capWarning.value = '当前服务未启用实时端点。请用 --serve-mode standard --enable-stream 启动后刷新本页。';
          } else {
            capWarning.value = '';
          }
        } catch (e) { /* 服务未起，忽略 */ }
      }
      onMounted(precheck);
      onBeforeUnmount(() => { cleanupMic(); closeWs(); stopDiag(); });

      return {
        lang, canIdentify, identifySpeakers,
        streamState, statusText, busy, source,
        capWarning, hint, capInfo, diag, vuRef,
        finals, partial, fmtMs, transcriptRef, spkIdx,
        logs, logOpen, logRef,
        streamFile, streamFileList, streamFileSize, onStreamUploadChange,
        noThrottle, fileProgress, fileRunning,
        startMic, stopMic, startFile, stopFile, forceClose,
      };
    },
    template: `
      <div class="page-flex">
        <n-alert v-if="capWarning" type="warning" :show-icon="true" style="margin-bottom:16px;">{{ capWarning }}</n-alert>

        <n-card :bordered="false" class="panel" size="small" style="margin-bottom:20px;">
          <div class="console-row">
            <span class="status-pill" :class="streamState"><span class="dot"></span>{{ statusText }}</span>
            <div v-if="diag.on" class="diag-row">
              <n-statistic label="发送速率" :value="diag.sent"><template #suffix><span class="diag-unit">帧/s</span></template></n-statistic>
              <n-statistic label="接收速率" :value="diag.recv"><template #suffix><span class="diag-unit">条/s</span></template></n-statistic>
              <n-statistic label="发送缓冲" :value="diag.buf"><template #suffix><span class="diag-unit">KB</span></template></n-statistic>
              <n-statistic label="最大帧" :value="diag.frame"><template #suffix><span class="diag-unit">样本</span></template></n-statistic>
              <n-statistic label="主线程卡顿" :value="diag.stall"><template #suffix><span class="diag-unit">ms</span></template></n-statistic>
            </div>
          </div>
          <n-text v-if="capInfo" depth="3" style="display:block;margin-top:10px;font-size:.76em;">{{ capInfo }}</n-text>
        </n-card>

        <div class="workspace">
          <div class="side-col">
            <n-card :bordered="false" class="panel" size="small">
              <template #header><span class="panel-title"><a-icon name="mic" size="15"></a-icon>输入源</span></template>
              <n-input v-model:value="lang" size="small" placeholder="语言（默认 auto，如 zh / en）" style="margin-bottom:12px;"></n-input>
              <n-checkbox v-if="canIdentify" v-model:checked="identifySpeakers" size="small" :disabled="busy" style="margin-bottom:12px;">
                声纹识别（真名标注）
              </n-checkbox>
              <n-tabs v-model:value="source" type="segment" size="small">
                <n-tab-pane name="mic" tab="麦克风" :disabled="busy && source !== 'mic'">
                  <n-space vertical size="large" style="margin-top:12px;">
                    <n-button v-if="!busy" id="micStart" type="primary" size="large" block strong @click="startMic">
                      <a-icon name="mic" size="15" style="margin-right:7px;"></a-icon>开始录音
                    </n-button>
                    <n-button v-else type="error" size="large" block strong @click="streamState === 'stopping' ? forceClose() : stopMic()">
                      <a-icon name="stop" size="15" style="margin-right:7px;"></a-icon>{{ streamState === 'stopping' ? '强制断开' : '停止录音' }}
                    </n-button>
                    <canvas ref="vuRef" class="vu-canvas" width="300" height="12"></canvas>
                    <n-text depth="3" style="font-size:.78em;">点击后授权麦克风，边说边转写。</n-text>
                  </n-space>
                </n-tab-pane>
                <n-tab-pane name="file" tab="文件模拟" :disabled="busy && source !== 'file'">
                  <n-space vertical size="medium" style="margin-top:12px;">
                    <!-- 不设 :max="1"：达到 max 后 n-upload 会禁用触发器导致无法换文件；
                         替换语义由 onStreamUploadChange 取末项实现（列表恒 ≤1） -->
                    <n-upload :file-list="streamFileList" :default-upload="false" :show-file-list="false"
                              :disabled="busy || fileRunning" accept="audio/*" @change="onStreamUploadChange">
                      <n-upload-dragger>
                        <div style="color:#14b8a6;margin-bottom:6px;"><a-icon name="file" size="26"></a-icon></div>
                        <n-text style="font-size:.88em;font-weight:600;">点击或拖拽选择音频文件</n-text>
                        <n-p depth="3" style="font-size:.74em;margin:5px 0 0;">解码后按 200ms 分帧模拟实时推流</n-p>
                      </n-upload-dragger>
                    </n-upload>
                    <div v-if="streamFile" class="file-meta" style="margin-top:0;">
                      <a-icon name="file" size="14"></a-icon>
                      <span class="file-name" :title="streamFile.name">{{ streamFile.name }}</span>
                      <n-tag size="tiny" :bordered="false">{{ streamFileSize }}</n-tag>
                    </div>
                    <n-checkbox v-model:checked="noThrottle" size="small">不限速（自适应最大速率）</n-checkbox>
                    <n-button v-if="!busy && !fileRunning" type="primary" size="large" block strong @click="startFile">
                      <a-icon name="play" size="15" style="margin-right:7px;"></a-icon>开始模拟推流
                    </n-button>
                    <n-button v-else type="error" size="large" block strong @click="streamState === 'stopping' ? forceClose() : stopFile()">
                      <a-icon name="stop" size="15" style="margin-right:7px;"></a-icon>{{ streamState === 'stopping' ? '强制断开' : '停止' }}
                    </n-button>
                    <n-progress v-if="fileRunning || fileProgress > 0" type="line" :percentage="fileProgress" :height="8" :border-radius="4" :show-indicator="false"></n-progress>
                    <n-text depth="3" style="font-size:.74em;line-height:1.6;">
                      按需加载 ffmpeg-wasm 解码（需外网，首次约 25–30MB，仅本次会话加载一次；失败自动回退浏览器原生解码）→ 转 16k 单声道 → 200ms 分帧推流，模拟实时输入。勾选不限速时按服务端积压上限自适应控速，不会触发 backlog_overflow。
                    </n-text>
                  </n-space>
                </n-tab-pane>
              </n-tabs>
              <n-alert v-if="hint" type="error" :show-icon="true" style="margin-top:12px;">{{ hint }}</n-alert>
            </n-card>
          </div>

          <div class="main-col">
            <n-card :bordered="false" class="panel" content-class="panel-body" size="small">
              <template #header><span class="panel-title"><a-icon name="doc" size="15"></a-icon>转写结果</span></template>
              <div id="transcript" ref="transcriptRef">
                <n-empty v-if="!finals.length && !partial" description="等待音频输入…" size="small" style="margin:24px 0;"></n-empty>
                <div v-for="line in finals" :key="line.key" class="transcript-line">
                  <span class="t">{{ line.start != null ? fmtMs(line.start) : '' }}</span>
                  <span class="tx"><span v-if="line.speaker" class="speaker-badge" :class="'spk-' + spkIdx(line.speaker)">{{ line.speakerName || line.speaker }}</span>{{ line.text }}<n-text v-if="line.words" depth="3" style="font-size:.78em;"> ({{ line.words }} 词)</n-text></span>
                </div>
                <div v-if="partial" class="partial-line">{{ partial }}<span class="cursor-blk"></span></div>
              </div>
            </n-card>
          </div>
        </div>

        <n-card :bordered="false" class="panel dock-card" :class="{ open: logOpen }" content-class="dock-content" size="small" style="margin-top:20px;">
          <n-space justify="space-between" align="center">
            <n-button text style="font-size:.95em;font-weight:600;" @click="logOpen = !logOpen">
              <a-icon name="doc" size="15" style="margin-right:7px;color:#14b8a6;"></a-icon>协议日志
              <a-icon name="chev" size="13" :style="{ marginLeft: '7px', transition: 'transform .2s', transform: logOpen ? 'rotate(180deg)' : 'none' }"></a-icon>
            </n-button>
            <n-button v-if="logOpen" size="tiny" tertiary @click="logs.length = 0">清空</n-button>
          </n-space>
          <div v-if="logOpen" ref="logRef" class="proto-log dock-body" style="margin-top:10px;">
            <div v-for="l in logs" :key="l.key" :class="l.kind">{{ l.ts }} {{ l.kind === 'send' ? '→' : l.kind === 'recv' ? '←' : '•' }} {{ l.text }}</div>
            <div v-if="!logs.length">（暂无消息）</div>
          </div>
        </n-card>
      </div>`,
  };

  mountApp(AppBody);
})();
