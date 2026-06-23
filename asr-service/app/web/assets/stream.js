/* 实时转写页（Vue 3 + Naive UI，无构建 UMD）。
 * 协议：WS /v2/asr/stream（?token= 鉴权），start/binary/stop 信封；
 * 麦克风经 AudioWorklet（/web-ui/assets/pcm-worklet.js）采集，主线程重采样 16k Int16；
 * 文件模拟推流优先 ffmpeg-wasm（CDN 懒加载，失败回退浏览器原生解码），200ms 分帧 + 1MB 背压。
 */
(function () {
  'use strict';
  const { ref, reactive, computed, watch, onMounted, onBeforeUnmount } = Vue;
  const { fmtMs, fmtBytes, spkIdx, apiKey, mountApp, makeT, locale } = window.AsrCommon;

  const M = {
    zh: {
      // 页面标题
      'page.title': '实时转写 - Qwen3-ASR Service',
      // 能力告警
      'cap.warning': '当前服务未启用实时端点。请用 --serve-mode standard --enable-stream 启动后刷新本页。',
      // 状态条（statusKey → 文案）
      'st.idle': '未连接', 'st.connecting': '连接中…', 'st.connected': '已连接 · {0}',
      'st.recording': '录音中…', 'st.pushing': '推流中…', 'st.loadingFf': '正在加载转码器 (ffmpeg-wasm)…',
      'st.micStopped': '已停止，等待末段结果…', 'st.fileDone': '推流完成，等待末段结果…',
      'st.fileStopped': '已停止', 'st.disconnected': '已断开', 'st.disconnectedCode': '已断开 ({0})',
      'st.sessionClosed': '会话结束',
      // 会话协议卡片：标量标签（版本/模式/后端/采样率；协议 ID 以等宽原样呈现，无需标签）
      'cap.version': 'v', 'cap.mode': '模式', 'cap.backend': '后端',
      'cap.sampleRate': '采样率', 'cap.capabilities': '能力',
      // 会话协议卡片（输入源下方）
      'sess.title': '会话协议',
      'cap.flag.partial_results': '增量结果', 'cap.flag.word_timestamps': '词级时间戳',
      'cap.flag.languages_auto': '自动语种', 'cap.flag.speaker_labels': '说话人分离',
      'cap.flag.speaker_identification': '声纹识别', 'cap.flag.noise_filter_tunable': '降噪可调',
      'cap.flag.speaker_tunable': '说话人可调', 'cap.flag.endpoint_tunable': '断句可调',
      'cap.flag.output_toggles': '输出可控', 'cap.flag.scene': '场景识别',
      'scene.silence': '静音', 'scene.speech': '语音', 'scene.singing': '歌唱',
      'scene.music': '音乐', 'scene.other': '其它',
      'scene.preset': '场景预设',
      'preset.balanced': '均衡（人声优先）', 'preset.live': '直播（人声优先+清唱偏置）',
      'preset.music': '音乐优先',
      'cap.tip.partial_results': '需 vLLM 模式才支持（当前 vad-offline 后端按段输出，不产增量结果）',
      // 诊断指标
      'diag.sent': '发送速率', 'diag.recv': '接收速率', 'diag.buf': '发送缓冲',
      'diag.frame': '最大帧', 'diag.stall': '渲染延迟',
      'diag.unit.frame': '帧/s', 'diag.unit.msg': '条/s', 'diag.unit.kb': 'KB',
      'diag.unit.sample': '样本', 'diag.unit.ms': 'ms',
      // 输入源面板
      'panel.input': '输入源', 'input.langPlaceholder': '语言（默认 auto，如 zh / en）',
      'input.identify': '声纹识别（真名标注）',
      'input.returnId': '返回声纹 ID（UUID，供客户端记忆）',
      'input.appendMode': '追加输出（保留上次结果，分隔续写）',
      // 声纹登记面板
      'spk.panel': '声纹登记', 'spk.empty': '说话后此处列出本场说话人',
      'spk.consent': '我已获得说话人同意（声纹属生物识别信息）',
      'spk.namePlaceholder': '输入姓名',
      'spk.enroll': '登记', 'spk.enrolled': '已登记',
      'spk.needConsent': '请先勾选「已获得说话人同意」',
      'spk.idTip': '点击复制 UUID', 'spk.copied': '已复制 UUID',
      // 高级设置（随 start 消息按会话覆盖）
      'adv.title': '高级设置（可选覆盖）',
      'adv.hint': '留空＝用服务端默认；关闭开关＝本次会话不执行该步骤。会话进行中不可改。',
      'adv.farfield': '远场降噪', 'adv.noiseFilter': '段级降噪过滤', 'adv.energyFloor': '能量门 (dBFS)', 'adv.snrMin': '信噪比门 (dB)',
      'adv.timing': '断句 / 分段', 'adv.endSilence': '断句尾静音 (ms)', 'adv.segmentSec': '长句切分 (秒)',
      'adv.speaker': '说话人', 'adv.diarize': '说话人分离', 'adv.spkThreshold': '归簇阈值 (余弦 0.2–0.9)', 'adv.spkMinSeg': '短段门槛 (ms)', 'adv.spkMax': '说话人上限',
      'adv.idThreshold': '识别阈值 (余弦 0–1)', 'adv.idMargin': '区分余量 (余弦 0–1)',
      'adv.output': '输出内容', 'adv.punc': '标点恢复', 'adv.words': '词级时间戳', 'adv.default': '默认',
      'warn.ignored': '部分参数因功能未启用被忽略：{0}',
      'tab.mic': '麦克风', 'tab.file': '文件模拟',
      // 麦克风
      'mic.start': '开始录音', 'mic.stop': '停止录音', 'mic.forceClose': '强制断开',
      'mic.hint': '点击后授权麦克风，边说边转写。',
      // 文件模拟
      'file.dragHint': '点击或拖拽选择音频文件',
      'file.frameNote': '解码后按 200ms 分帧模拟实时推流',
      'file.useFfmpeg': '用 ffmpeg-wasm 解码（兼容更多格式）',
      'file.ffmpegTip': '遇到浏览器无法解码的封装/编码时启用。按需加载 ffmpeg-wasm 转码器：需外网，首次约 25–30MB，仅本次会话加载一次；加载失败自动回退浏览器原生解码。',
      'file.ffLoading': '正在加载转码器 (ffmpeg-wasm)，首次约 25–30MB…',
      'file.noThrottle': '不限速（自适应最大速率）',
      'file.start': '开始模拟推流', 'file.stop': '停止', 'file.forceClose': '强制断开',
      'file.longHint': '默认浏览器原生解码 → 转 16k 单声道 → 200ms 分帧推流，模拟实时输入。勾选不限速时按服务端积压上限自适应控速，不会触发 backlog_overflow。',
      // 提示与错误
      'err.micAccess': '麦克风访问失败: {0}', 'err.worklet': 'AudioWorklet 加载失败: {0}',
      'err.noFile': '请先选择音频文件。', 'err.decode': '音频解码失败: {0}',
      'err.code': '[{0}] {1}',
      'err.authFailed': '鉴权失败：请检查 API Key。',
      'err.concurrencyFull': '并发会话已满（1013）。',
      'err.notReady': '实时端点未就绪：请用 --serve-mode standard --enable-stream 启动服务。',
      // 转写结果
      'panel.result': '转写结果', 'result.waiting': '等待音频输入…', 'result.words': '({0} 词)', 'result.divider': '新一段',
      // 协议日志
      'log.title': '协议日志', 'log.clear': '清空', 'log.empty': '（暂无消息）',
    },
    en: {
      'page.title': 'Live Transcription - Qwen3-ASR Service',
      'cap.warning': 'The live endpoint is not enabled. Start with --serve-mode standard --enable-stream, then refresh this page.',
      'st.idle': 'Disconnected', 'st.connecting': 'Connecting…', 'st.connected': 'Connected · {0}',
      'st.recording': 'Recording…', 'st.pushing': 'Streaming…', 'st.loadingFf': 'Loading transcoder (ffmpeg-wasm)…',
      'st.micStopped': 'Stopped, waiting for final segment…', 'st.fileDone': 'Streaming done, waiting for final segment…',
      'st.fileStopped': 'Stopped', 'st.disconnected': 'Disconnected', 'st.disconnectedCode': 'Disconnected ({0})',
      'st.sessionClosed': 'Session closed',
      'cap.version': 'v', 'cap.mode': 'Mode', 'cap.backend': 'Backend',
      'cap.sampleRate': 'Sample rate', 'cap.capabilities': 'Capabilities',
      'sess.title': 'Session protocol',
      'cap.flag.partial_results': 'Partial results', 'cap.flag.word_timestamps': 'Word timestamps',
      'cap.flag.languages_auto': 'Auto language', 'cap.flag.speaker_labels': 'Speaker labels',
      'cap.flag.speaker_identification': 'Speaker ID', 'cap.flag.noise_filter_tunable': 'Denoise tunable',
      'cap.flag.speaker_tunable': 'Speaker tunable', 'cap.flag.endpoint_tunable': 'Endpoint tunable',
      'cap.flag.output_toggles': 'Output toggles', 'cap.flag.scene': 'Scene',
      'scene.silence': 'Silence', 'scene.speech': 'Speech', 'scene.singing': 'Singing',
      'scene.music': 'Music', 'scene.other': 'Other',
      'scene.preset': 'Scene preset',
      'preset.balanced': 'Balanced (vocal-priority)', 'preset.live': 'Live (vocal + a-cappella bias)',
      'preset.music': 'Music-first',
      'cap.tip.partial_results': 'Requires vLLM mode (the current vad-offline backend emits per segment, not incrementally)',
      'diag.sent': 'Send rate', 'diag.recv': 'Recv rate', 'diag.buf': 'Send buffer',
      'diag.frame': 'Max frame', 'diag.stall': 'Render lag',
      'diag.unit.frame': 'fr/s', 'diag.unit.msg': 'msg/s', 'diag.unit.kb': 'KB',
      'diag.unit.sample': 'samples', 'diag.unit.ms': 'ms',
      'panel.input': 'Input source', 'input.langPlaceholder': 'Language (default auto, e.g. zh / en)',
      'input.identify': 'Speaker identification (label real names)',
      'input.returnId': 'Return voiceprint ID (UUID, for the client to remember)',
      'input.appendMode': 'Append output (keep previous results, continue after a divider)',
      'spk.panel': 'Voiceprint enrollment', 'spk.empty': 'Speakers will be listed here once they talk',
      'spk.consent': 'I have the speaker’s consent (voiceprint is biometric data)',
      'spk.namePlaceholder': 'Enter a name',
      'spk.enroll': 'Enroll', 'spk.enrolled': 'Enrolled',
      'spk.needConsent': 'Please confirm consent first',
      'spk.idTip': 'Click to copy UUID', 'spk.copied': 'UUID copied',
      'adv.title': 'Advanced (optional overrides)',
      'adv.hint': 'Empty = server default; turning a switch off skips that step. Locked while a session is active.',
      'adv.farfield': 'Far-field denoise', 'adv.noiseFilter': 'Segment denoise gate', 'adv.energyFloor': 'Energy gate (dBFS)', 'adv.snrMin': 'SNR gate (dB)',
      'adv.timing': 'Endpoint / segment', 'adv.endSilence': 'End silence (ms)', 'adv.segmentSec': 'Max segment (s)',
      'adv.speaker': 'Speaker', 'adv.diarize': 'Speaker diarization', 'adv.spkThreshold': 'Cluster threshold (cosine 0.2–0.9)', 'adv.spkMinSeg': 'Min segment (ms)', 'adv.spkMax': 'Max speakers',
      'adv.idThreshold': 'ID threshold (cosine 0–1)', 'adv.idMargin': 'ID margin (cosine 0–1)',
      'adv.output': 'Output', 'adv.punc': 'Punctuation', 'adv.words': 'Word timestamps', 'adv.default': 'default',
      'warn.ignored': 'Some params were ignored (feature not enabled): {0}',
      'tab.mic': 'Microphone', 'tab.file': 'File simulation',
      'mic.start': 'Start recording', 'mic.stop': 'Stop recording', 'mic.forceClose': 'Force disconnect',
      'mic.hint': 'Grant microphone access, then speak to transcribe live.',
      'file.dragHint': 'Click or drag to select an audio file',
      'file.frameNote': 'Decoded then framed at 200ms to simulate live streaming',
      'file.useFfmpeg': 'Decode with ffmpeg-wasm (more formats)',
      'file.ffmpegTip': 'Enable for containers/codecs the browser cannot decode. Lazily loads the ffmpeg-wasm transcoder: needs internet, ~25–30MB on first use, loaded once per session; falls back to native decoding on failure.',
      'file.ffLoading': 'Loading transcoder (ffmpeg-wasm), ~25–30MB on first use…',
      'file.noThrottle': 'Unthrottled (adaptive max rate)',
      'file.start': 'Start simulated streaming', 'file.stop': 'Stop', 'file.forceClose': 'Force disconnect',
      'file.longHint': 'Defaults to native browser decoding → converts to 16k mono → frames at 200ms to simulate live input. When unthrottled, the rate adapts to the server backlog limit without triggering backlog_overflow.',
      'err.micAccess': 'Microphone access failed: {0}', 'err.worklet': 'Failed to load AudioWorklet: {0}',
      'err.noFile': 'Please select an audio file first.', 'err.decode': 'Audio decoding failed: {0}',
      'err.code': '[{0}] {1}',
      'err.authFailed': 'Authentication failed: please check the API Key.',
      'err.concurrencyFull': 'Concurrent sessions are full (1013).',
      'err.notReady': 'Live endpoint not ready: start the service with --serve-mode standard --enable-stream.',
      'panel.result': 'Transcription', 'result.waiting': 'Waiting for audio input…', 'result.words': '({0} words)', 'result.divider': 'New session',
      'log.title': 'Protocol log', 'log.clear': 'Clear', 'log.empty': '(no messages)',
    },
  };
  const t = makeT(M);

  // 派生场景：已知五桶给本地化标签 + 固定配色 class，未知值回退原文 + other 配色
  const SCENE_KEYS = ['silence', 'speech', 'singing', 'music', 'other'];
  function sceneLabel(s) { return SCENE_KEYS.includes(s) ? t('scene.' + s) : s; }
  function sceneCls(s) { return 'scene-' + (SCENE_KEYS.includes(s) ? s : 'other'); }
  function sceneTags(scores, exclude) {
    if (!scores) return [];
    return Object.entries(scores)
      .filter(([k, v]) => v >= 0.10 && k !== exclude)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: k, pct: Math.round(v * 100) }));
  }
  // 主场景桶的概率后缀（该桶有分即显示，含文本救回的低分歌声；非内容桶 silence/other 无分则只留标签）
  function scenePct(scores, key) {
    return (scores && scores[key] != null) ? ' ' + Math.round(scores[key] * 100) + '%' : '';
  }

  const RT_SR = 16000;
  const FRAME = 3200;                 // 200ms @16k
  const BP_LIMIT = 1 << 20;           // 1MB 发送缓冲上限（背压）
  const MAX_LOG_LINES = 300;
  const MAX_TRANSCRIPT_LINES = 200;
  // ffmpeg-wasm 资源走 npmmirror（阿里云，国内可达且 CORS 开放，三包齐全）。
  // unpkg.zhimg 只镜像 @ffmpeg/core，缺 @ffmpeg/ffmpeg 与 @ffmpeg/util（请求 404 → 转码器静默失效），故整体改用 npmmirror。
  const ffFile = (pkg, ver, path) => 'https://registry.npmmirror.com/' + pkg + '/' + ver + '/files/' + path;
  // ⚠️ 升级版本必读：worker chunk 名 '814.ffmpeg.js' 是 @ffmpeg/ffmpeg 的 webpack 产物名，与版本强耦合——
  //    升级 FF_VER 后该 chunk 名很可能改变，必须同步核对（看 dist/umd 目录），否则 worker 404 → 转码器静默回退原生。
  //    @ffmpeg/ffmpeg 与 @ffmpeg/core 同版本（FF_VER）；@ffmpeg/util 版本独立（FF_UTIL_VER）。
  const FF_VER = '0.12.10';
  const FF_UTIL_VER = '0.12.1';
  const FF_FFMPEG_JS = ffFile('@ffmpeg/ffmpeg', FF_VER, 'dist/umd/ffmpeg.js');
  const FF_WORKER_JS = ffFile('@ffmpeg/ffmpeg', FF_VER, 'dist/umd/814.ffmpeg.js');
  const FF_UTIL_JS = ffFile('@ffmpeg/util', FF_UTIL_VER, 'dist/umd/index.js');
  // core 取 ESM 构建（含 default 导出）：模块 worker 内无 importScripts，靠 import() 加载 core
  const FF_CORE_JS = ffFile('@ffmpeg/core', FF_VER, 'dist/esm/ffmpeg-core.js');
  const FF_CORE_WASM = ffFile('@ffmpeg/core', FF_VER, 'dist/esm/ffmpeg-core.wasm');
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
      // 返回声纹 UUID（仅 identify 开启才有意义）+ 显式登记所需的同意与命名
      const returnSpeakerId = ref(false);
      const consent = ref(false);
      const enrollName = reactive({});    // label -> 输入框姓名
      const enrolledMap = reactive({});   // label -> {name, speakerId}（命中/登记后回填，含历史行）
      // identify 关闭时联动复位 returnSpeakerId（无识别即无 id 可回传）
      watch(identifySpeakers, (on) => { if (!on) returnSpeakerId.value = false; });
      // —— 高级设置：能力门控标志（precheck 填充）+ 按会话覆盖值（null=不下发，用服务端默认）——
      const srv = reactive({ punc: false, words: false, speaker: false, speakerDb: false, scene: false, defaults: {} });
      // 场景预设（下拉，按会话覆盖）：默认随服务端生效预设；空列表=未暴露则隐藏
      const scenePresets = ref([]);
      const scenePreset = ref('');
      const scenePresetOptions = computed(() =>
        scenePresets.value.map(p => ({ value: p, label: t('preset.' + p) === 'preset.' + p ? p : t('preset.' + p) })));
      const adv = reactive({
        noiseFilter: false, energyFloor: null, snrMin: null,
        spkThreshold: null, spkMinSeg: null, spkMax: null, idThreshold: null, idMargin: null,
        maxEndSilence: null, maxSegmentSec: null,
        withPunc: true, withWords: true, diarize: true,   // 降级开关：默认开，关闭才下发 false
      });
      // 关闭说话人分离时联动复位声纹识别——identify 依赖 diarize，否则会把 identify_speakers=true
      // 与 diarize=false 一并下发，服务端静默丢弃，UI 却仍显示识别开启
      watch(() => adv.diarize, (on) => { if (!on) identifySpeakers.value = false; });
      const warn = ref('');               // 非致命软提示（params_ignored）
      // 数值框占位：有服务端默认值时直接显示该值（留空即用此值），否则显示「默认」
      function ph(key) {
        const d = srv.defaults[key];
        return d != null ? String(d) : t('adv.default');
      }

      // —— 会话状态机：idle | connecting | streaming | stopping ——
      const streamState = ref('idle');
      // 状态条存 key + 细节而非冻结的中文串：切语言时 statusText 经 t() 即时重渲染
      const statusKey = ref('idle');
      const statusDetail = ref('');
      const statusText = computed(() => t('st.' + statusKey.value, statusDetail.value));
      const source = ref('mic');          // mic | file
      const busy = computed(() => streamState.value !== 'idle');

      // —— 提示与能力 ——
      // streamDisabled 存布尔状态而非冻结文案：capWarning 经 t() 随语言切换重渲染
      const streamDisabled = ref(false);
      const capWarning = computed(() => (streamDisabled.value ? t('cap.warning') : ''));
      const hint = ref('');
      // capParts 存 session.created 原始部件：标量字段直接渲染，capabilities 经 capFlags 转芯片
      const capParts = ref(null);         // {protocol, version, mode, backend, sampleRate, capabilities}
      // 能力芯片渲染顺序；已知键给本地化短标签，未知键回退原始键名（漏配可见不报错）
      const CAP_ORDER = [
        'languages_auto', 'partial_results', 'word_timestamps',
        'speaker_labels', 'speaker_identification',
        'noise_filter_tunable', 'speaker_tunable', 'endpoint_tunable', 'output_toggles', 'scene',
      ];
      const capFlags = computed(() => {
        const c = capParts.value && capParts.value.capabilities;
        if (!c) return [];
        const known = CAP_ORDER.filter(k => k in c);
        const extra = Object.keys(c).filter(k => !CAP_ORDER.includes(k));
        // 已启用排前：成排亮色芯片更易扫读，禁用项弱化随后（sort 稳定，组内保持 CAP_ORDER）
        // 目前仅「增量结果」带「为何不可用」说明（需 vLLM）；后续若多了再抽成集合/由服务端下发
        return known.concat(extra)
          .map(k => ({ key: k, label: t('cap.flag.' + k), on: !!c[k], tip: k === 'partial_results' ? t('cap.tip.partial_results') : '' }))
          .sort((a, b) => Number(b.on) - Number(a.on));
      });

      // —— 结果 ——
      const finals = reactive([]);        // {key, start, text, words, speaker, speakerName, scene, sceneScores}
      const partial = ref('');
      const appendMode = ref(false);      // 追加输出：开始新会话时不清空结果，按批次派生分隔线续写
      let finalSeq = 0, batchSeq = 0;     // batchSeq：每次追加新会话 +1，渲染层据相邻条目 batch 变化插分隔线
      function appendFinal(m) {
        finals.push({ key: ++finalSeq, batch: batchSeq, start: m.start, text: m.text || '', words: (m.words && m.words.length) || 0, speaker: m.speaker || null, speakerName: m.speaker_name || null, speakerId: m.speaker_id || null, sceneScores: m.scene_scores || null, scene: m.scene || null });
        // 命中/已登记的簇回填会话级映射（驱动登记面板与历史行真名展示）
        if (m.speaker && (m.speaker_name || m.speaker_id)) {
          const prev = enrolledMap[m.speaker] || {};
          enrolledMap[m.speaker] = { name: m.speaker_name || prev.name || null, speakerId: m.speaker_id || prev.speakerId || null };
        }
        if (finals.length > MAX_TRANSCRIPT_LINES) finals.shift();
      }
      function clearResults() { finals.length = 0; partial.value = ''; batchSeq = 0; Object.keys(enrolledMap).forEach(k => delete enrolledMap[k]); Object.keys(enrollName).forEach(k => delete enrollName[k]); }
      // 本场说话人（按 finals 出现去重）+ 命中/登记态，驱动登记面板
      const sessionSpeakers = computed(() => {
        const seen = new Map();
        for (const f of finals) {
          if (!f.speaker) continue;
          const e = enrolledMap[f.speaker] || {};
          seen.set(f.speaker, { label: f.speaker, name: e.name || f.speakerName || null, speakerId: e.speakerId || f.speakerId || null });
        }
        return Array.from(seen.values());
      });
      function enrollSpeaker(label) {
        if (!consent.value) { hint.value = t('spk.needConsent'); return; }
        const name = (enrollName[label] || '').trim();
        if (!name) return;
        wsSendJson({ type: 'enroll', label, name, consent: true });
      }
      function copyId(id) {
        if (id && navigator.clipboard) navigator.clipboard.writeText(id).then(() => { hint.value = ''; }).catch(() => {});
      }

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

      // —— 诊断指标（n-statistic 横排：发送/接收速率、WS 缓冲、最大帧、渲染延迟＝主线程 rAF 间隔峰值）——
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
        if (returnSpeakerId.value) m.return_speaker_id = true;
        // 远场降噪
        if (adv.noiseFilter) m.noise_filter = true;
        if (adv.energyFloor != null) m.energy_floor_dbfs = adv.energyFloor;
        if (adv.snrMin != null) m.snr_min_db = adv.snrMin;
        // 说话人
        if (adv.spkThreshold != null) m.speaker_threshold = adv.spkThreshold;
        if (adv.spkMinSeg != null) m.speaker_min_seg_ms = adv.spkMinSeg;
        if (adv.spkMax != null) m.speaker_max = adv.spkMax;
        if (adv.idThreshold != null) m.speaker_id_threshold = adv.idThreshold;
        if (adv.idMargin != null) m.speaker_id_margin = adv.idMargin;
        // 断句 / 分段
        if (adv.maxEndSilence != null) m.max_end_silence_ms = adv.maxEndSilence;
        if (adv.maxSegmentSec != null) m.max_segment_sec = adv.maxSegmentSec;
        // 输出降级：仅功能已加载且用户关闭时下发 false（不下发＝沿用服务端默认）
        if (srv.punc && adv.withPunc === false) m.with_punc = false;
        if (srv.words && adv.withWords === false) m.with_words = false;
        if (srv.speaker && adv.diarize === false) m.diarize = false;
        // 场景预设（按会话覆盖服务端默认）
        if (srv.scene && scenePreset.value) m.scene_preset = scenePreset.value;
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
        warn.value = '';
        // 追加输出：保留上次结果、递增批次号（渲染层据 batch 变化派生分隔线，无内容则不显示）；否则照常清空
        if (appendMode.value && finals.length) {
          partial.value = '';
          batchSeq++;
        } else {
          clearResults();
        }
        pushedBytes = 0; pushStartTs = 0; procEndMs = 0;   // 流控状态按会话重置
        const t = apiKey.value.trim();
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        const url = proto + '://' + location.host + '/v2/asr/stream' + (t ? '?token=' + encodeURIComponent(t) : '');
        streamState.value = 'connecting';
        statusKey.value = 'connecting'; statusDetail.value = '';
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
            capParts.value = {
              protocol: m.protocol, version: m.protocol_version, mode: m.mode,
              backend: m.backend, sampleRate: m.sample_rate, capabilities: m.capabilities || {},
            };
            statusKey.value = 'connected'; statusDetail.value = m.backend;
            streamState.value = 'streaming';
            if (onReady) onReady();
          } else if (m.type === 'partial') {
            partial.value = m.text || '';
          } else if (m.type === 'final') {
            if (m.end != null && m.end > procEndMs) procEndMs = m.end;   // 服务端处理进度反馈
            appendFinal(m);
            partial.value = '';
          } else if (m.type === 'enroll.ack') {
            // 登记成功：回填映射与历史行（同会话同标签即时显示真名/UUID）
            enrolledMap[m.label] = { name: m.name, speakerId: m.speaker_id };
            for (const f of finals) if (f.speaker === m.label) { f.speakerName = m.name; f.speakerId = m.speaker_id; }
            enrollName[m.label] = '';
          } else if (m.type === 'error') {
            // 仅 params_ignored（功能未启用的覆盖项）为软提示，单独展示；其余 error
            // （含非致命的 feed_failed 等）一律走错误提示，避免真实错误被伪装成警告
            if (m.code === 'params_ignored') {
              const i = (m.message || '').indexOf(': ');
              warn.value = i >= 0 ? m.message.slice(i + 2) : (m.message || '');
            } else {
              hint.value = t('err.code', m.code, m.message);
            }
          } else if (m.type === 'session.closed') {
            statusKey.value = 'sessionClosed'; statusDetail.value = '';
          }
        };
        ws.onerror = () => log('evt', 'WS error');
        ws.onclose = ev => {
          log('evt', 'WS close code=' + ev.code);
          statusKey.value = 'disconnectedCode'; statusDetail.value = ev.code;
          if (ev.code === 1008) hint.value = t('err.authFailed');
          else if (ev.code === 1013) hint.value = t('err.concurrencyFull');
          else if (ev.code === 1011) hint.value = t('err.notReady');
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
        statusKey.value = 'disconnected'; statusDetail.value = '';
      }

      // —— 麦克风（AudioWorklet 外置文件）——
      let micCtx = null, micNode = null, micSrc = null, micStream = null;
      async function startMic() {
        hint.value = '';
        try {
          micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e) {
          hint.value = t('err.micAccess', e.message);
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
            hint.value = t('err.worklet', e.message);
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
          statusKey.value = 'recording'; statusDetail.value = '';
        });
      }
      function stopMic() {
        streamState.value = 'stopping';
        wsSendJson({ type: 'stop' });
        cleanupMic();
        statusKey.value = 'micStopped'; statusDetail.value = '';
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
      let ffmpeg = null;
      const ffLoading = ref(false);       // 转码器加载中（首次约 25–30MB）：驱动文件区加载提示
      function loadScript(src) {
        return new Promise((res, rej) => {
          const s = document.createElement('script');
          s.src = src; s.async = true;
          s.onload = res;
          s.onerror = () => rej(new Error('script load failed: ' + src));
          document.head.appendChild(s);
        });
      }
      async function getFFmpeg() {
        if (ffmpeg) return ffmpeg;
        if (ffLoading.value) return null;       // 加载进行中：避免并发重入（不永久闩锁，失败后下次仍可重试）
        ffLoading.value = true;
        try {
          statusKey.value = 'loadingFf'; statusDetail.value = '';
          // 两个 UMD 脚本互不依赖，并行注入（已加载则跳过）
          await Promise.all([
            window.FFmpegWASM ? null : loadScript(FF_FFMPEG_JS),
            window.FFmpegUtil ? null : loadScript(FF_UTIL_JS),
          ]);
          const { FFmpeg } = window.FFmpegWASM;
          const { toBlobURL } = window.FFmpegUtil;
          const ff = new FFmpeg();
          ff.on('log', ({ message }) => log('evt', 'ffmpeg: ' + message));
          // 跨域 CDN 无构建：必须传 classWorkerURL(blob) 走模块 worker——直接 new Worker(跨域URL) 会被浏览器同源策略拦截；
          // 模块 worker 内 importScripts 不可用，FFmpeg 回退 import() 加载 core，故 coreURL 必须指向 ESM 构建。
          // 三个 blob 互不依赖，并行下载（~30MB wasm 不必等 worker/core 下完）
          const [classWorkerURL, coreURL, wasmURL] = await Promise.all([
            toBlobURL(FF_WORKER_JS, 'text/javascript'),
            toBlobURL(FF_CORE_JS, 'text/javascript'),
            toBlobURL(FF_CORE_WASM, 'application/wasm'),
          ]);
          await ff.load({ classWorkerURL, coreURL, wasmURL });
          ffmpeg = ff;
          log('evt', 'ffmpeg-wasm ready');
          return ff;
        } catch (e) {
          // 仅本次返回 null 回退原生，不闩锁——网络恢复后用户再传文件可重新尝试加载
          log('evt', 'ffmpeg-wasm load failed, falling back to native browser decoding: ' + e.message);
          return null;
        } finally {
          ffLoading.value = false;
        }
      }
      // 浏览器原生解码（Web Audio）→ 16k 单声道 PCM16
      async function decodeNativeTo16kPCM(file) {
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
      // 文件 → 16k 单声道 PCM16。默认浏览器原生解码；用户勾选「ffmpeg 解码」才按需加载
      // ffmpeg-wasm（兼容浏览器无法解码的封装/编码，直出 s16le），加载失败回退原生。
      async function decodeFileTo16kPCM(file) {
        if (useFfmpeg.value) {
          const ff = await getFFmpeg();
          if (ff) {
            const { fetchFile } = window.FFmpegUtil;
            await ff.writeFile('input', await fetchFile(file));
            await ff.exec(['-i', 'input', '-ac', '1', '-ar', String(RT_SR), '-f', 's16le', 'out.pcm']);
            const out = await ff.readFile('out.pcm');
            try { await ff.deleteFile('input'); await ff.deleteFile('out.pcm'); } catch (e) { /* noop */ }
            return new Int16Array(out.buffer, out.byteOffset, Math.floor(out.byteLength / 2));
          }
          // ffmpeg 加载失败：已记入协议日志，回退原生解码
        }
        return decodeNativeTo16kPCM(file);
      }

      // —— 文件模拟推流 ——
      const streamFile = ref(null);
      const streamFileList = ref([]);
      const noThrottle = ref(false);
      const useFfmpeg = ref(false);       // 默认浏览器原生解码；勾选才用 ffmpeg-wasm（兼容更多格式）
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
        if (!file) { hint.value = t('err.noFile'); return; }
        fileRunning.value = true;
        fileProgress.value = 0;
        fileAborted = false;

        let pcm16;
        try {
          pcm16 = await decodeFileTo16kPCM(file);
        } catch (e) {
          hint.value = t('err.decode', e.message);
          fileRunning.value = false;
          statusKey.value = 'idle'; statusDetail.value = '';
          return;
        }
        if (fileAborted) { fileRunning.value = false; return; }

        openWs(async () => {
          wsSendJson(startMsg());
          statusKey.value = 'pushing'; statusDetail.value = '';
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
          statusKey.value = 'fileDone'; statusDetail.value = '';
          fileRunning.value = false;
        });
      }
      function stopFile() {
        fileAborted = true;
        streamState.value = 'stopping';
        wsSendJson({ type: 'stop' });
        statusKey.value = 'fileStopped'; statusDetail.value = '';
        fileRunning.value = false;
      }

      // —— 能力预检 ——
      async function precheck() {
        try {
          const r = await fetch('/v2/capabilities');
          if (r.ok) {
            const c = await r.json();
            canIdentify.value = !!c.speaker_identification;
            streamDisabled.value = !(c.stream && c.stream.enabled);
            srv.words = !!(c.stream && c.stream.word_timestamps);
            srv.speaker = !!c.speaker_labels;
            srv.speakerDb = !!c.speaker_identification;
            srv.scene = !!(c.stream && c.stream.scene);
            scenePresets.value = c.scene_presets || [];
            scenePreset.value = c.scene_preset || (scenePresets.value[0] || '');
            srv.defaults = c.defaults || {};
          }
          const h = await fetch('/v2/health');     // punc 能力仅 health 暴露
          if (h.ok) srv.punc = !!(await h.json()).punc_enabled;
        } catch (e) { /* 服务未起，忽略 */ }
      }
      onMounted(precheck);
      onBeforeUnmount(() => { cleanupMic(); closeWs(); stopDiag(); });

      // 页面标题本地化：随语言切换更新 document.title
      const setTitle = () => { document.title = t('page.title'); };
      setTitle();
      watch(locale, setTitle);

      return {
        t,
        lang, canIdentify, identifySpeakers, returnSpeakerId, consent, enrollName, enrolledMap,
        sessionSpeakers, enrollSpeaker, copyId,
        appendMode, srv, adv, warn, ph,
        scenePreset, scenePresetOptions,
        streamState, statusText, busy, source,
        capWarning, hint, capParts, capFlags, diag, vuRef,
        finals, partial, sceneLabel, sceneCls, sceneTags, scenePct, fmtMs, transcriptRef, spkIdx,
        logs, logOpen, logRef,
        streamFile, streamFileList, streamFileSize, onStreamUploadChange,
        noThrottle, useFfmpeg, ffLoading, fileProgress, fileRunning,
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
              <n-statistic :label="t('diag.sent')" :value="diag.sent"><template #suffix><span class="diag-unit">{{ t('diag.unit.frame') }}</span></template></n-statistic>
              <n-statistic :label="t('diag.recv')" :value="diag.recv"><template #suffix><span class="diag-unit">{{ t('diag.unit.msg') }}</span></template></n-statistic>
              <n-statistic :label="t('diag.buf')" :value="diag.buf"><template #suffix><span class="diag-unit">{{ t('diag.unit.kb') }}</span></template></n-statistic>
              <n-statistic :label="t('diag.frame')" :value="diag.frame"><template #suffix><span class="diag-unit">{{ t('diag.unit.sample') }}</span></template></n-statistic>
              <n-statistic :label="t('diag.stall')" :value="diag.stall"><template #suffix><span class="diag-unit">{{ t('diag.unit.ms') }}</span></template></n-statistic>
            </div>
          </div>
        </n-card>

        <div class="workspace">
          <div class="side-col">
            <n-card :bordered="false" class="panel" size="small">
              <template #header><span class="panel-title"><a-icon name="mic" size="15"></a-icon>{{ t('panel.input') }}</span></template>
              <n-input v-model:value="lang" size="small" :placeholder="t('input.langPlaceholder')" style="margin-bottom:12px;"></n-input>
              <div v-if="srv.scene && scenePresetOptions.length" class="adv-field" style="margin-bottom:12px;">
                <span class="lbl">{{ t('scene.preset') }}</span>
                <n-select v-model:value="scenePreset" :options="scenePresetOptions" size="small" :disabled="busy" style="width:188px;"></n-select>
              </div>
              <n-checkbox v-if="canIdentify" v-model:checked="identifySpeakers" size="small" :disabled="busy || !adv.diarize" style="margin-bottom:12px;">
                {{ t('input.identify') }}
              </n-checkbox>
              <n-checkbox v-if="canIdentify && identifySpeakers" v-model:checked="returnSpeakerId" size="small" :disabled="busy" style="margin-bottom:12px;">
                {{ t('input.returnId') }}
              </n-checkbox>
              <n-checkbox v-model:checked="appendMode" size="small" :disabled="busy" style="margin-bottom:12px;">
                {{ t('input.appendMode') }}
              </n-checkbox>
              <n-collapse style="margin-bottom:12px;">
                <n-collapse-item :title="t('adv.title')" name="adv">
                  <div class="adv-hint">{{ t('adv.hint') }}</div>
                  <div class="sec-title">{{ t('adv.farfield') }}</div>
                  <div class="adv-field"><span class="lbl">{{ t('adv.noiseFilter') }}</span><n-switch v-model:value="adv.noiseFilter" size="small" :disabled="busy"></n-switch></div>
                  <template v-if="adv.noiseFilter">
                    <div class="adv-field"><span class="lbl">{{ t('adv.energyFloor') }}</span><n-input-number v-model:value="adv.energyFloor" size="small" :min="-90" :max="0" clearable :placeholder="ph('energy_floor_dbfs')" :disabled="busy" style="width:128px;"></n-input-number></div>
                    <div class="adv-field"><span class="lbl">{{ t('adv.snrMin') }}</span><n-input-number v-model:value="adv.snrMin" size="small" :min="0" :max="40" clearable :placeholder="ph('snr_min_db')" :disabled="busy" style="width:128px;"></n-input-number></div>
                  </template>
                  <div class="sec-title">{{ t('adv.timing') }}</div>
                  <div class="adv-field"><span class="lbl">{{ t('adv.endSilence') }}</span><n-input-number v-model:value="adv.maxEndSilence" size="small" :min="200" :max="2000" :step="50" clearable :placeholder="ph('max_end_silence_ms')" :disabled="busy" style="width:128px;"></n-input-number></div>
                  <div class="adv-field"><span class="lbl">{{ t('adv.segmentSec') }}</span><n-input-number v-model:value="adv.maxSegmentSec" size="small" :min="1" :max="60" clearable :placeholder="ph('max_segment_sec')" :disabled="busy" style="width:128px;"></n-input-number></div>
                  <template v-if="srv.speaker">
                    <div class="sec-title">{{ t('adv.speaker') }}</div>
                    <div class="adv-field"><span class="lbl">{{ t('adv.diarize') }}</span><n-switch v-model:value="adv.diarize" size="small" :disabled="busy"></n-switch></div>
                    <div class="adv-field"><span class="lbl" :class="{ muted: !adv.diarize }">{{ t('adv.spkThreshold') }}</span><n-input-number v-model:value="adv.spkThreshold" size="small" :min="0.2" :max="0.9" :step="0.05" clearable :placeholder="ph('speaker_threshold')" :disabled="busy || !adv.diarize" style="width:128px;"></n-input-number></div>
                    <div class="adv-field"><span class="lbl" :class="{ muted: !adv.diarize }">{{ t('adv.spkMinSeg') }}</span><n-input-number v-model:value="adv.spkMinSeg" size="small" :min="0" :max="10000" :step="100" clearable :placeholder="ph('speaker_min_seg_ms')" :disabled="busy || !adv.diarize" style="width:128px;"></n-input-number></div>
                    <div class="adv-field"><span class="lbl" :class="{ muted: !adv.diarize }">{{ t('adv.spkMax') }}</span><n-input-number v-model:value="adv.spkMax" size="small" :min="1" :max="50" clearable :placeholder="ph('speaker_max')" :disabled="busy || !adv.diarize" style="width:128px;"></n-input-number></div>
                    <template v-if="srv.speakerDb">
                      <div class="adv-field"><span class="lbl" :class="{ muted: !adv.diarize }">{{ t('adv.idThreshold') }}</span><n-input-number v-model:value="adv.idThreshold" size="small" :min="0" :max="1" :step="0.05" clearable :placeholder="ph('speaker_id_threshold')" :disabled="busy || !adv.diarize" style="width:128px;"></n-input-number></div>
                      <div class="adv-field"><span class="lbl" :class="{ muted: !adv.diarize }">{{ t('adv.idMargin') }}</span><n-input-number v-model:value="adv.idMargin" size="small" :min="0" :max="1" :step="0.05" clearable :placeholder="ph('speaker_id_margin')" :disabled="busy || !adv.diarize" style="width:128px;"></n-input-number></div>
                    </template>
                  </template>
                  <template v-if="srv.punc || srv.words">
                    <div class="sec-title">{{ t('adv.output') }}</div>
                    <div v-if="srv.punc" class="adv-field"><span class="lbl">{{ t('adv.punc') }}</span><n-switch v-model:value="adv.withPunc" size="small" :disabled="busy"></n-switch></div>
                    <div v-if="srv.words" class="adv-field"><span class="lbl">{{ t('adv.words') }}</span><n-switch v-model:value="adv.withWords" size="small" :disabled="busy"></n-switch></div>
                  </template>
                </n-collapse-item>
              </n-collapse>
              <n-tabs v-model:value="source" type="segment" size="small">
                <n-tab-pane name="mic" :tab="t('tab.mic')" :disabled="busy && source !== 'mic'">
                  <n-space vertical size="large" style="margin-top:12px;">
                    <n-button v-if="!busy" id="micStart" type="primary" size="large" block strong @click="startMic">
                      <a-icon name="mic" size="15" style="margin-right:7px;"></a-icon>{{ t('mic.start') }}
                    </n-button>
                    <n-button v-else type="error" size="large" block strong @click="streamState === 'stopping' ? forceClose() : stopMic()">
                      <a-icon name="stop" size="15" style="margin-right:7px;"></a-icon>{{ streamState === 'stopping' ? t('mic.forceClose') : t('mic.stop') }}
                    </n-button>
                    <canvas ref="vuRef" class="vu-canvas" width="300" height="12"></canvas>
                    <n-text depth="3" style="font-size:.78em;">{{ t('mic.hint') }}</n-text>
                  </n-space>
                </n-tab-pane>
                <n-tab-pane name="file" :tab="t('tab.file')" :disabled="busy && source !== 'file'">
                  <n-space vertical size="medium" style="margin-top:12px;">
                    <!-- 不设 :max="1"：达到 max 后 n-upload 会禁用触发器导致无法换文件；
                         替换语义由 onStreamUploadChange 取末项实现（列表恒 ≤1） -->
                    <n-upload :file-list="streamFileList" :default-upload="false" :show-file-list="false"
                              :disabled="busy || fileRunning" accept="audio/*" @change="onStreamUploadChange">
                      <n-upload-dragger>
                        <div style="color:#14b8a6;margin-bottom:6px;"><a-icon name="file" size="26"></a-icon></div>
                        <n-text style="font-size:.88em;font-weight:600;">{{ t('file.dragHint') }}</n-text>
                        <n-p depth="3" style="font-size:.74em;margin:5px 0 0;">{{ t('file.frameNote') }}</n-p>
                      </n-upload-dragger>
                    </n-upload>
                    <div v-if="streamFile" class="file-meta" style="margin-top:0;">
                      <a-icon name="file" size="14"></a-icon>
                      <span class="file-name" :title="streamFile.name">{{ streamFile.name }}</span>
                      <n-tag size="tiny" :bordered="false">{{ streamFileSize }}</n-tag>
                    </div>
                    <div class="ff-opt">
                      <n-checkbox v-model:checked="useFfmpeg" size="small" :disabled="busy || fileRunning">{{ t('file.useFfmpeg') }}</n-checkbox>
                      <n-popover trigger="hover" placement="top">
                        <template #trigger><span class="info-dot"><a-icon name="info" size="15"></a-icon></span></template>
                        <div class="ff-tip">{{ t('file.ffmpegTip') }}</div>
                      </n-popover>
                    </div>
                    <div class="ff-opt">
                      <n-checkbox v-model:checked="noThrottle" size="small">{{ t('file.noThrottle') }}</n-checkbox>
                      <n-popover trigger="hover" placement="top">
                        <template #trigger><span class="info-dot"><a-icon name="info" size="15"></a-icon></span></template>
                        <div class="ff-tip">{{ t('file.longHint') }}</div>
                      </n-popover>
                    </div>
                    <n-button v-if="!busy && !fileRunning" type="primary" size="large" block strong @click="startFile">
                      <a-icon name="play" size="15" style="margin-right:7px;"></a-icon>{{ t('file.start') }}
                    </n-button>
                    <n-button v-else type="error" size="large" block strong @click="streamState === 'stopping' ? forceClose() : stopFile()">
                      <a-icon name="stop" size="15" style="margin-right:7px;"></a-icon>{{ streamState === 'stopping' ? t('file.forceClose') : t('file.stop') }}
                    </n-button>
                    <div v-if="ffLoading" class="ff-loading"><n-spin :size="14"></n-spin><span>{{ t('file.ffLoading') }}</span></div>
                    <n-progress v-if="fileRunning || fileProgress > 0" type="line" :percentage="fileProgress" :height="8" :border-radius="4" :show-indicator="false"></n-progress>
                  </n-space>
                </n-tab-pane>
              </n-tabs>
              <n-alert v-if="hint" type="error" :show-icon="true" style="margin-top:12px;">{{ hint }}</n-alert>
              <n-alert v-if="warn" type="warning" :show-icon="true" :bordered="false" style="margin-top:12px;">{{ t('warn.ignored', warn) }}</n-alert>
            </n-card>

            <n-card v-if="canIdentify && identifySpeakers" :bordered="false" class="panel" size="small">
              <template #header><span class="panel-title"><a-icon name="mic" size="15"></a-icon>{{ t('spk.panel') }}</span></template>
              <n-checkbox v-model:checked="consent" size="small" style="margin-bottom:10px;">{{ t('spk.consent') }}</n-checkbox>
              <n-empty v-if="!sessionSpeakers.length" :description="t('spk.empty')" size="small" style="margin:8px 0;"></n-empty>
              <div v-for="sp in sessionSpeakers" :key="sp.label" class="spk-enroll-row" style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                <span class="speaker-badge" :class="'spk-' + spkIdx(sp.label)">{{ sp.name || sp.label }}</span>
                <template v-if="sp.name">
                  <n-tag size="tiny" type="success" :bordered="false">{{ t('spk.enrolled') }}</n-tag>
                  <n-text v-if="sp.speakerId" depth="3" :title="t('spk.idTip')" style="font-size:.72em;cursor:pointer;font-family:monospace;" @click="copyId(sp.speakerId)">{{ sp.speakerId.slice(0, 8) }}…</n-text>
                </template>
                <template v-else>
                  <n-input v-model:value="enrollName[sp.label]" size="tiny" :placeholder="t('spk.namePlaceholder')" style="flex:1;min-width:80px;"></n-input>
                  <n-button size="tiny" type="primary" :disabled="!consent || !(enrollName[sp.label] || '').trim()" @click="enrollSpeaker(sp.label)">{{ t('spk.enroll') }}</n-button>
                </template>
              </div>
            </n-card>

            <n-card v-if="capParts" :bordered="false" class="panel sess-card" size="small">
              <template #header><span class="panel-title"><a-icon name="chip" size="15"></a-icon>{{ t('sess.title') }}</span></template>
              <div class="sess-proto">
                <span class="id">{{ capParts.protocol }}</span>
                <span class="ver">{{ t('cap.version') }}{{ capParts.version }}</span>
              </div>
              <div class="sess-meta">
                <span class="k">{{ t('cap.mode') }}</span><span class="v">{{ capParts.mode }}</span>
                <span class="k">{{ t('cap.backend') }}</span><span class="v">{{ capParts.backend }}</span>
                <span class="k">{{ t('cap.sampleRate') }}</span><span class="v">{{ capParts.sampleRate }} Hz</span>
              </div>
              <template v-if="capFlags.length">
                <div class="sess-caps-label">{{ t('cap.capabilities') }}</div>
                <div class="sess-caps">
                  <n-tooltip v-for="f in capFlags" :key="f.key" trigger="hover" :disabled="!f.tip">
                    <template #trigger><span class="cap-chip" :class="[f.on ? 'on' : 'off', { 'has-tip': f.tip }]">{{ f.label }}</span></template>
                    {{ f.tip }}
                  </n-tooltip>
                </div>
              </template>
            </n-card>
          </div>

          <div class="main-col">
            <n-card :bordered="false" class="panel" content-class="panel-body" size="small">
              <template #header><span class="panel-title"><a-icon name="doc" size="15"></a-icon>{{ t('panel.result') }}</span></template>
              <div id="transcript" ref="transcriptRef">
                <n-empty v-if="!finals.length && !partial" :description="t('result.waiting')" size="small" style="margin:24px 0;"></n-empty>
                <template v-for="(line, i) in finals" :key="line.key">
                  <div v-if="i > 0 && line.batch !== finals[i - 1].batch" class="transcript-divider"><span>{{ t('result.divider') }}</span></div>
                  <div class="transcript-line">
                    <span class="t">{{ line.start != null ? fmtMs(line.start) : '' }}</span>
                    <span class="tx"><span v-if="line.scene" class="scene-badge" :class="sceneCls(line.scene)" :title="sceneLabel(line.scene)">{{ sceneLabel(line.scene) }}{{ scenePct(line.sceneScores, line.scene) }}</span><span v-for="tag in sceneTags(line.sceneScores, line.scene)" :key="tag.label" class="scene-badge" :class="sceneCls(tag.label)" :title="sceneLabel(tag.label)">{{ sceneLabel(tag.label) }} {{ tag.pct }}%</span><span v-if="line.speaker" class="speaker-badge" :class="'spk-' + spkIdx(line.speaker)" :title="line.speakerId ? line.speakerId + ' · ' + t('spk.idTip') : ''" :style="line.speakerId ? 'cursor:pointer' : ''" @click="line.speakerId && copyId(line.speakerId)">{{ line.speakerName || line.speaker }}</span>{{ line.text }}<n-text v-if="line.words" depth="3" style="font-size:.78em;"> {{ t('result.words', line.words) }}</n-text></span>
                  </div>
                </template>
                <div v-if="partial" class="partial-line">{{ partial }}<span class="cursor-blk"></span></div>
              </div>
            </n-card>
          </div>
        </div>

        <n-card :bordered="false" class="panel dock-card" :class="{ open: logOpen }" content-class="dock-content" size="small" style="margin-top:20px;">
          <n-space justify="space-between" align="center">
            <n-button text style="font-size:.95em;font-weight:600;" @click="logOpen = !logOpen">
              <a-icon name="doc" size="15" style="margin-right:7px;color:#14b8a6;"></a-icon>{{ t('log.title') }}
              <a-icon name="chev" size="13" :style="{ marginLeft: '7px', transition: 'transform .2s', transform: logOpen ? 'rotate(180deg)' : 'none' }"></a-icon>
            </n-button>
            <n-button v-if="logOpen" size="tiny" tertiary @click="logs.length = 0">{{ t('log.clear') }}</n-button>
          </n-space>
          <div v-if="logOpen" ref="logRef" class="proto-log dock-body" style="margin-top:10px;">
            <div v-for="l in logs" :key="l.key" :class="l.kind">{{ l.ts }} {{ l.kind === 'send' ? '→' : l.kind === 'recv' ? '←' : '•' }} {{ l.text }}</div>
            <div v-if="!logs.length">{{ t('log.empty') }}</div>
          </div>
        </n-card>
      </div>`,
  };

  mountApp(AppBody);
})();
