/* 离线转写页（Vue 3 + Naive UI，无构建 UMD）。
 * 端点：POST /v2/asr、GET/DELETE /v2/tasks/{id}、GET /v2/tasks?history=true
 * 任务列表自适应轮询：有 pending/processing 任务 3s，全终态 30s；后台标签页暂停。
 */
(function () {
  'use strict';
  const { ref, reactive, computed, watch, onMounted, onBeforeUnmount, h } = Vue;
  const { fmtTime, fmtMs, fmtDate, fmtBytes, spkIdx, authHeaders, mountApp, makeT, locale } = window.AsrCommon;

  const M = {
    zh: {
      // 结果展示
      'meta.language': '语言: {0}', 'meta.align': '对齐: {0}', 'meta.punc': '标点: {0}',
      'meta.speakers': '说话人: {0}', 'meta.duration': '时长: {0}s',
      'meta.on': '开启', 'meta.off': '关闭',
      'result.segments': '分段结果', 'result.noSegments': '无分段数据',
      'result.fullText': '完整文本', 'result.rawJson': '原始 JSON', 'result.downloadJson': '下载 JSON',
      'spk.anonymous': '匿名说话人', 'spk.autoEnrolled': '自动登记（可在说话人管理页改名）',
      'spk.similarity': '声纹相似度 {0}',
      // 音频标注（派生场景 + 事件段）
      'scene.silence': '静音', 'scene.speech': '语音', 'scene.singing': '歌唱',
      'scene.music': '音乐', 'scene.other': '其它',
      'result.audioEvents': '音频事件', 'result.sceneTimeline': '场景时间线',
      'result.noEvents': '未检出音频事件',
      // 上传
      'upload.title': '上传音频', 'upload.hint': '点击或拖拽上传音频文件',
      'upload.formats': 'wav / mp3 / flac / m4a / aac / ogg / wma / amr / opus',
      'upload.identify': '声纹识别（标注真名，未知说话人自动登记）',
      'upload.tagOnly': '仅标注（不转写）', 'upload.tagScene': '包含场景时间线',
      'action.tagging': '标注中…', 'action.tag': '开始标注',
      'scene.preset': '场景预设',
      'preset.balanced': '均衡（人声优先）', 'preset.live': '直播（人声优先+清唱偏置）',
      'preset.music': '音乐优先',
      // 高级设置（可选按请求覆盖）
      'adv.title': '高级设置（可选覆盖）',
      'adv.hint': '留空＝用服务端默认；关闭开关＝本次不执行该步骤。',
      'adv.output': '输出内容', 'adv.punc': '标点恢复', 'adv.words': '词级时间戳', 'adv.diarize': '说话人分离',
      'adv.tuning': '分段与识别', 'adv.segment': '分段时长（秒）',
      'adv.idThreshold': '识别阈值 (余弦 0–1)', 'adv.idMargin': '区分余量 (余弦 0–1)', 'adv.default': '默认',
      'warn.ignored': '部分参数因功能未启用被忽略：{0}',
      // 识别动作
      'action.recognizing': '识别中…', 'action.start': '开始识别',
      'action.cancelling': '取消中…', 'action.cancel': '取消识别',
      // 结果区
      'result.title': '识别结果', 'result.failed': '识别失败',
      'result.uploading': '上传中…', 'result.progress': '识别中 {0}%',
      'result.empty': '上传音频并开始识别',
      // 消息提示
      'msg.uploadFailed': '上传失败', 'msg.recognizeFailed': '识别失败',
      'msg.cancelled': '任务已取消', 'msg.cancelledPartial': '任务已取消，已显示部分结果',
      'msg.taskNotFound': '任务不存在', 'msg.cancelledNoResult': '任务已取消，无结果',
      'msg.unknownStatus': '未知任务状态: {0}', 'msg.statusEmpty': '(空)',
      'msg.pollFailed': '轮询失败: {0}', 'msg.cancelSendFailed': '取消请求发送失败: {0}',
      'msg.listLoadFailed': '任务列表加载失败: {0}',
      'msg.taskExpired': '任务不存在或已过期', 'msg.loadFailed': '加载失败: {0}',
      'msg.deleted': '已删除', 'msg.taskDeleted': '任务不存在或已被删除',
      'msg.deleteFailed': '删除失败: {0}',
      // 状态
      'status.pending': '排队中', 'status.processing': '处理中', 'status.completed': '已完成',
      'status.failed': '失败', 'status.cancelled': '已取消',
      // 任务历史
      'task.title': '任务历史', 'task.refresh': '刷新',
      'filter.all': '全部',
      'col.file': '文件 / 任务', 'col.status': '状态', 'col.progress': '进度',
      'col.createdAt': '创建时间', 'col.actions': '操作',
      'col.view': '查看', 'col.delete': '删除',
      'task.deleteTitle': '删除任务记录',
      'task.deleteConfirm': '确定删除任务 {0} 吗？进行中的任务将被取消，历史记录将被删除。',
      'task.deletePositive': '删除', 'task.deleteNegative': '取消',
      'task.pollNote': '列表自动刷新：进行中任务每 3 秒，空闲每 30 秒；含持久化历史记录',
      // 任务详情弹窗
      'viewer.title': '任务详情：{0}',
      'viewer.taskId': '任务 ID', 'viewer.status': '状态', 'viewer.progress': '进度',
    },
    en: {
      'meta.language': 'Language: {0}', 'meta.align': 'Align: {0}', 'meta.punc': 'Punctuation: {0}',
      'meta.speakers': 'Speakers: {0}', 'meta.duration': 'Duration: {0}s',
      'meta.on': 'on', 'meta.off': 'off',
      'result.segments': 'Segments', 'result.noSegments': 'No segment data',
      'result.fullText': 'Full text', 'result.rawJson': 'Raw JSON', 'result.downloadJson': 'Download JSON',
      'spk.anonymous': 'Anonymous speaker', 'spk.autoEnrolled': 'Auto-enrolled (rename on the Speakers page)',
      'spk.similarity': 'Voiceprint similarity {0}',
      'scene.silence': 'Silence', 'scene.speech': 'Speech', 'scene.singing': 'Singing',
      'scene.music': 'Music', 'scene.other': 'Other',
      'result.audioEvents': 'Audio events', 'result.sceneTimeline': 'Scene timeline',
      'result.noEvents': 'No audio events detected',
      'upload.title': 'Upload audio', 'upload.hint': 'Click or drag to upload an audio file',
      'upload.formats': 'wav / mp3 / flac / m4a / aac / ogg / wma / amr / opus',
      'upload.identify': 'Speaker identification (label real names, auto-enroll unknowns)',
      'upload.tagOnly': 'Tag only (no transcription)', 'upload.tagScene': 'Include scene timeline',
      'action.tagging': 'Tagging…', 'action.tag': 'Start tagging',
      'scene.preset': 'Scene preset',
      'preset.balanced': 'Balanced (vocal-priority)', 'preset.live': 'Live (vocal + a-cappella bias)',
      'preset.music': 'Music-first',
      'adv.title': 'Advanced (optional overrides)',
      'adv.hint': 'Empty = server default; turning a switch off skips that step for this request.',
      'adv.output': 'Output', 'adv.punc': 'Punctuation', 'adv.words': 'Word timestamps', 'adv.diarize': 'Speaker diarization',
      'adv.tuning': 'Segment & identification', 'adv.segment': 'Segment length (s)',
      'adv.idThreshold': 'ID threshold (cosine 0–1)', 'adv.idMargin': 'ID margin (cosine 0–1)', 'adv.default': 'default',
      'warn.ignored': 'Some params were ignored (feature not enabled): {0}',
      'action.recognizing': 'Recognizing…', 'action.start': 'Start',
      'action.cancelling': 'Cancelling…', 'action.cancel': 'Cancel',
      'result.title': 'Result', 'result.failed': 'Recognition failed',
      'result.uploading': 'Uploading…', 'result.progress': 'Recognizing {0}%',
      'result.empty': 'Upload audio and start recognition',
      'msg.uploadFailed': 'Upload failed', 'msg.recognizeFailed': 'Recognition failed',
      'msg.cancelled': 'Task cancelled', 'msg.cancelledPartial': 'Task cancelled, partial result shown',
      'msg.taskNotFound': 'Task not found', 'msg.cancelledNoResult': 'Task cancelled, no result',
      'msg.unknownStatus': 'Unknown task status: {0}', 'msg.statusEmpty': '(empty)',
      'msg.pollFailed': 'Polling failed: {0}', 'msg.cancelSendFailed': 'Failed to send cancel request: {0}',
      'msg.listLoadFailed': 'Failed to load task list: {0}',
      'msg.taskExpired': 'Task not found or expired', 'msg.loadFailed': 'Load failed: {0}',
      'msg.deleted': 'Deleted', 'msg.taskDeleted': 'Task not found or already deleted',
      'msg.deleteFailed': 'Delete failed: {0}',
      'status.pending': 'Queued', 'status.processing': 'Processing', 'status.completed': 'Completed',
      'status.failed': 'Failed', 'status.cancelled': 'Cancelled',
      'task.title': 'Task history', 'task.refresh': 'Refresh',
      'filter.all': 'All',
      'col.file': 'File / Task', 'col.status': 'Status', 'col.progress': 'Progress',
      'col.createdAt': 'Created', 'col.actions': 'Actions',
      'col.view': 'View', 'col.delete': 'Delete',
      'task.deleteTitle': 'Delete task record',
      'task.deleteConfirm': 'Delete task {0}? Running tasks will be cancelled and history removed.',
      'task.deletePositive': 'Delete', 'task.deleteNegative': 'Cancel',
      'task.pollNote': 'Auto-refresh: 3s for running tasks, 30s when idle; includes persisted history',
      'viewer.title': 'Task details: {0}',
      'viewer.taskId': 'Task ID', 'viewer.status': 'Status', 'viewer.progress': 'Progress',
    },
  };
  const t = makeT(M);

  // 派生场景：已知五桶给本地化标签 + 固定配色 class，未知值回退原文 + other 配色
  const SCENE_KEYS = ['silence', 'speech', 'singing', 'music', 'other'];
  function sceneLabel(s) { return SCENE_KEYS.includes(s) ? t('scene.' + s) : s; }
  function sceneCls(s) { return 'scene-' + (SCENE_KEYS.includes(s) ? s : 'other'); }
  // 各桶概率 → 多标签（降序，过滤 <10% 噪声，排除已作主标签的桶），渲染成「音乐 31% · 说话 10%」
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

  const STATUS_TAG_TYPES = { pending: 'warning', processing: 'info', completed: 'success', failed: 'error', cancelled: 'default' };
  function statusLabel(s) { const v = t('status.' + s); return v === 'status.' + s ? s : v; }
  const POLL_ACTIVE_MS = 3000;   // 列表含进行中任务时的刷新间隔
  const POLL_IDLE_MS = 30000;    // 全终态时的低频刷新（外部客户端新建任务也能感知）

  /* 结果展示（分段时间轴 + 全文/JSON 折叠），当前任务与历史查看弹窗复用；onSeek 仅当前任务传入 */
  const ResultView = {
    props: { data: { type: Object, required: true }, onSeek: { type: Function, default: null } },
    setup(props) {
      const result = computed(() => props.data.result || {});
      const segments = computed(() => result.value.segments || []);
      const audioEvents = computed(() => result.value.audio_events || []);
      const sceneTimeline = computed(() => result.value.scene_timeline || []);
      // 转写结果至少有「分段」概念；仅当三者皆空才提示无分段（仅标注模式没有 segments 是正常的）
      const isEmpty = computed(() => !segments.value.length && !audioEvents.value.length && !sceneTimeline.value.length);
      const metaTags = computed(() => {
        const r = result.value, tags = [];
        if (r.language) tags.push(t('meta.language', r.language));
        if (r.align_enabled != null) tags.push(t('meta.align', r.align_enabled ? t('meta.on') : t('meta.off')));
        if (r.punc_enabled != null) tags.push(t('meta.punc', r.punc_enabled ? t('meta.on') : t('meta.off')));
        if (r.speakers && r.speakers.length) tags.push(t('meta.speakers', r.speakers.length));
        if (r.duration != null) tags.push(t('meta.duration', r.duration.toFixed(1)));
        return tags;
      });
      const jsonText = computed(() => JSON.stringify(props.data, null, 2));
      function downloadJson() {
        const blob = new Blob([jsonText.value], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'asr_result.json';
        a.click();
        URL.revokeObjectURL(a.href);
      }
      function seek(seg) { if (props.onSeek && seg.start != null) props.onSeek(seg); }
      function seekEvent(ev) { if (props.onSeek && ev.start_ms != null) props.onSeek({ start: ev.start_ms / 1000 }); }
      // 声纹识别开启时 result.speakers 为映射表（含 score）；纯标签列表时为空映射
      const spkMeta = computed(() => {
        const map = {};
        (result.value.speakers || []).forEach(s => { if (s && typeof s === 'object') map[s.label] = s; });
        return map;
      });
      function spkTitle(seg) {
        const m = spkMeta.value[seg.speaker];
        if (!m) return seg.speaker_name ? '' : t('spk.anonymous');
        if (m.auto_enrolled) return t('spk.autoEnrolled');
        return m.score != null ? t('spk.similarity', m.score.toFixed(2)) : '';
      }
      return { result, segments, audioEvents, sceneTimeline, isEmpty, metaTags, jsonText, downloadJson,
               seek, seekEvent, fmtTime, fmtMs, spkIdx, spkTitle, sceneLabel, sceneCls, sceneTags, scenePct, t };
    },
    template: `
      <div>
        <n-space v-if="metaTags.length" size="small" style="margin-bottom:14px;">
          <n-tag v-for="t in metaTags" :key="t" size="small" :bordered="false">{{ t }}</n-tag>
        </n-space>
        <n-alert v-if="result.warnings && result.warnings.length" type="warning" size="small" :show-icon="true" :bordered="false" style="margin-bottom:14px;">
          {{ t('warn.ignored', result.warnings.join(', ')) }}
        </n-alert>
        <template v-if="segments.length">
          <div class="sec-title">{{ t('result.segments') }}</div>
          <div>
            <div v-for="(seg, i) in segments" :key="i" class="seg-row" :class="{ static: !onSeek }" @click="seek(seg)">
              <span class="seg-time">{{ fmtTime(seg.start) }}</span>
              <span class="seg-text"><span v-if="seg.scene" class="scene-badge" :class="sceneCls(seg.scene)" :title="sceneLabel(seg.scene)">{{ sceneLabel(seg.scene) }}{{ scenePct(seg.scene_scores, seg.scene) }}</span><span v-for="tag in sceneTags(seg.scene_scores, seg.scene)" :key="tag.label" class="scene-badge" :class="sceneCls(tag.label)" :title="sceneLabel(tag.label)">{{ sceneLabel(tag.label) }} {{ tag.pct }}%</span><span v-if="seg.speaker" class="speaker-badge" :class="'spk-' + spkIdx(seg.speaker)" :title="spkTitle(seg)">{{ seg.speaker_name || seg.speaker }}</span>{{ seg.text }}</span>
              <span v-if="onSeek" class="seg-play"><a-icon name="play" size="14"></a-icon></span>
            </div>
          </div>
        </template>
        <n-empty v-else-if="isEmpty" :description="t('result.noSegments')" size="small" style="margin:12px 0;"></n-empty>

        <template v-if="sceneTimeline.length">
          <div class="sec-title" style="margin-top:18px;">{{ t('result.sceneTimeline') }}</div>
          <div v-for="(ev, i) in sceneTimeline" :key="'sc' + i" class="event-row" :class="{ seekable: !!onSeek }" @click="seekEvent(ev)">
            <span class="event-span">{{ fmtMs(ev.start_ms) }} – {{ fmtMs(ev.end_ms) }}</span>
            <span class="scene-badge" :class="sceneCls(ev.label)">{{ sceneLabel(ev.label) }}</span>
            <span v-for="tag in sceneTags(ev.scene_scores, ev.label)" :key="tag.label" class="event-conf" style="margin-left:0;">{{ sceneLabel(tag.label) }} {{ tag.pct }}%</span>
          </div>
        </template>

        <template v-if="audioEvents.length">
          <div class="sec-title" style="margin-top:18px;">{{ t('result.audioEvents') }}</div>
          <div v-for="(ev, i) in audioEvents" :key="'ev' + i" class="event-row" :class="{ seekable: !!onSeek }" @click="seekEvent(ev)">
            <span class="event-span">{{ fmtMs(ev.start_ms) }} – {{ fmtMs(ev.end_ms) }}</span>
            <span class="seg-text">{{ ev.label }}</span>
            <span class="event-conf">{{ ev.confidence != null ? ev.confidence.toFixed(2) : '' }}</span>
          </div>
        </template>

        <n-collapse :default-expanded-names="['full']" style="margin-top:18px;">
          <n-collapse-item v-if="result.full_text" :title="t('result.fullText')" name="full">
            <div class="full-text">{{ result.full_text }}</div>
          </n-collapse-item>
          <n-collapse-item :title="t('result.rawJson')" name="json">
            <template #header-extra>
              <n-button size="tiny" tertiary @click.stop="downloadJson">{{ t('result.downloadJson') }}</n-button>
            </template>
            <pre class="json-pre">{{ jsonText }}</pre>
          </n-collapse-item>
        </n-collapse>
      </div>`,
  };

  const AppBody = {
    components: { 'result-view': ResultView },
    setup() {
      const message = naive.useMessage();
      const dialog = naive.useDialog();

      // —— 文件选择 ——
      const audioRef = ref(null);
      const selectedFile = ref(null);
      const uploadFileList = ref([]);
      const audioSrc = ref('');
      let audioObjectURL = null;
      function onUploadChange(payload) {
        const list = payload.fileList;
        const item = list.length ? list[list.length - 1] : null;
        uploadFileList.value = item ? [item] : [];
        if (item && item.file) {
          selectedFile.value = item.file;
          if (audioObjectURL) URL.revokeObjectURL(audioObjectURL);
          audioObjectURL = URL.createObjectURL(item.file);
          audioSrc.value = audioObjectURL;
          resetCurrent();
        } else {
          selectedFile.value = null;
          audioSrc.value = '';
        }
      }
      const fileSize = computed(() => (selectedFile.value ? fmtBytes(selectedFile.value.size) : ''));

      // —— 声纹识别（请求级 opt-in；capabilities 探测到 speaker_identification 才显示开关）——
      const canIdentify = ref(false);
      const identifySpeakers = ref(false);
      // —— 高级设置门控标志（/v2/health）+ 按请求覆盖值（null=不下发，用服务端默认）——
      const srv = reactive({ punc: false, align: false, speaker: false, speakerDb: false, tagging: false, defaults: {} });
      // 仅标注（不转写）：勾选后改调 /v2/audio/tag 同步返回事件/场景；tagScene 控制是否带场景时间线
      const tagOnly = ref(false);
      const tagScene = ref(true);
      // 场景预设（下拉）：默认随服务端生效预设，按请求下发覆盖；空列表=服务端未暴露则隐藏
      const scenePresets = ref([]);
      const scenePreset = ref('');
      const scenePresetOptions = computed(() =>
        scenePresets.value.map(p => ({ value: p, label: t('preset.' + p) === 'preset.' + p ? p : t('preset.' + p) })));
      const adv = reactive({
        withPunc: true, withWords: true, diarize: true,   // 降级开关：默认开，关闭才下发 false
        maxSegment: null, idThreshold: null, idMargin: null,
      });
      // 关闭说话人分离时联动复位声纹识别——identify 依赖 diarize，否则会把 identify_speakers=true
      // 与 diarize=false 一并下发，服务端静默丢弃，UI 却仍显示识别开启
      watch(() => adv.diarize, (on) => { if (!on) identifySpeakers.value = false; });
      // 数值框占位：有服务端默认值时直接显示该值（留空即用此值），否则显示「默认」
      function ph(key) {
        const d = srv.defaults[key];
        return d != null ? String(d) : t('adv.default');
      }
      onMounted(async () => {
        try {
          const r = await fetch('/v2/capabilities');
          if (r.ok) {
            const c = await r.json();
            canIdentify.value = !!c.speaker_identification;
            srv.tagging = !!c.audio_tagging;
            scenePresets.value = c.scene_presets || [];
            scenePreset.value = c.scene_preset || (scenePresets.value[0] || '');
            srv.defaults = c.defaults || {};
          }
        } catch (e) { /* 探测失败按不可用处理，开关保持隐藏 */ }
        try {
          const h = await fetch('/v2/health');
          if (h.ok) {
            const d = await h.json();
            srv.punc = !!d.punc_enabled; srv.align = !!d.align_enabled;
            srv.speaker = !!d.speaker_enabled; srv.speakerDb = !!d.speaker_db_enabled;
          }
        } catch (e) { /* 探测失败：高级开关保持隐藏，数值项仍可用 */ }
      });

      // —— 当前任务 ——
      // phase: idle | submitting | running | done | error
      const current = reactive({ taskId: null, phase: 'idle', progress: 0, error: '', data: null, cancelling: false });
      let detailTimer = null;
      function resetCurrent() {
        clearTimeout(detailTimer); detailTimer = null;
        Object.assign(current, { taskId: null, phase: 'idle', progress: 0, error: '', data: null, cancelling: false });
      }
      const progressPct = computed(() => Math.round((current.progress || 0) * 100));

      async function submit() {
        if (!selectedFile.value || current.phase === 'submitting' || current.phase === 'running') return;
        resetCurrent();
        current.phase = 'submitting';

        // 仅标注：同步端点，直接拿事件/场景渲染（无任务、无轮询）
        if (tagOnly.value) {
          const tform = new FormData();
          tform.append('file', selectedFile.value);
          tform.append('with_scene', tagScene.value ? 'true' : 'false');
          if (scenePreset.value) tform.append('scene_preset', scenePreset.value);
          try {
            const res = await fetch('/v2/audio/tag', { method: 'POST', body: tform, headers: authHeaders() });
            if (!res.ok) {
              const err = await res.json().catch(() => ({ detail: t('msg.recognizeFailed') }));
              throw new Error(err.detail || t('msg.recognizeFailed'));
            }
            current.data = { result: await res.json() };
            current.phase = 'done';
          } catch (e) {
            current.phase = 'error';
            current.error = e.message;
          }
          return;
        }

        const form = new FormData();
        form.append('file', selectedFile.value);
        if (identifySpeakers.value) form.append('identify_speakers', 'true');
        // 降级开关：仅在功能已加载且用户关闭时下发 false（不下发＝沿用服务端默认）
        if (srv.punc && adv.withPunc === false) form.append('with_punc', 'false');
        if (srv.align && adv.withWords === false) form.append('with_words', 'false');
        if (srv.speaker && adv.diarize === false) form.append('diarize', 'false');
        if (adv.maxSegment != null) form.append('max_segment', String(adv.maxSegment));
        if (adv.idThreshold != null) form.append('speaker_id_threshold', String(adv.idThreshold));
        if (adv.idMargin != null) form.append('speaker_id_margin', String(adv.idMargin));
        if (srv.tagging && scenePreset.value) form.append('scene_preset', scenePreset.value);
        try {
          const res = await fetch('/v2/asr', { method: 'POST', body: form, headers: authHeaders() });
          if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: t('msg.uploadFailed') }));
            throw new Error(err.detail || t('msg.uploadFailed'));
          }
          const data = await res.json();
          current.taskId = data.task_id;
          current.phase = 'running';
          startDetailPoll(data.task_id);
          pokeListPoll();   // 新任务入列，让列表立刻感知并切活动态轮询
        } catch (e) {
          current.phase = 'error';
          current.error = e.message;
        }
      }

      function finishPoll() { clearTimeout(detailTimer); detailTimer = null; }
      function startDetailPoll(taskId) {
        finishPoll();
        // setTimeout 自重排（同列表轮询）：上次请求完成后再排下次，慢后端时不会请求堆叠
        const tick = async () => {
          try {
            const res = await fetch('/v2/tasks/' + taskId, { headers: authHeaders() });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            if (data.status === 'processing' || data.status === 'pending' || data.status === 'queued') {
              current.progress = data.progress || 0;
              detailTimer = setTimeout(tick, 1000);
              return;
            }
            if (data.status === 'completed') {
              current.progress = 1;
              current.phase = 'done';
              current.data = data;
            } else if (data.status === 'cancelled') {
              if (data.result && data.result.segments && data.result.segments.length) {
                current.phase = 'done';
                current.data = data;
                message.info(t('msg.cancelledPartial'));
              } else {
                current.phase = 'error';
                current.error = data.error || t('msg.cancelled');
              }
            } else if (data.status === 'failed') {
              current.phase = 'error';
              current.error = data.error || t('msg.recognizeFailed');
            } else if (data.status === 'not_found') {
              current.phase = 'error';
              current.error = t('msg.taskNotFound');
            } else {
              // 未知状态（如鉴权失效后的非任务响应体）也终止，避免静默无限轮询
              current.phase = 'error';
              current.error = t('msg.unknownStatus', data.status || t('msg.statusEmpty'));
            }
          } catch (e) {
            current.phase = 'error';
            current.error = t('msg.pollFailed', e.message);
          }
        };
        detailTimer = setTimeout(tick, 1000);
      }

      async function cancelTask() {
        if (!current.taskId || current.cancelling) return;
        current.cancelling = true;
        try {
          await fetch('/v2/tasks/' + current.taskId, { method: 'DELETE', headers: authHeaders() });
          // 取消结果（cancelled / 带部分结果）由详情轮询判定
        } catch (e) {
          current.cancelling = false;   // 请求没送达服务端，恢复按钮可重试
          message.error(t('msg.cancelSendFailed', e.message));
        }
      }
      function seekAudio(seg) {
        const el = audioRef.value;
        if (el) { el.currentTime = seg.start; el.play(); }
      }

      // —— 任务列表（历史 + 自适应自动刷新）——
      const taskList = reactive({ open: false, rows: [], filter: '', loading: false, loaded: false });
      let listTimer = null;
      async function loadTaskList(notify) {
        taskList.loading = true;
        try {
          const url = '/v2/tasks?history=true&limit=50' + (taskList.filter ? '&status=' + taskList.filter : '');
          const res = await fetch(url, { headers: authHeaders() });
          if (!res.ok) throw new Error('HTTP ' + res.status);
          const data = await res.json();
          taskList.rows = data.tasks || [];
          taskList.loaded = true;
        } catch (e) {
          if (notify) message.error(t('msg.listLoadFailed', e.message));
        } finally {
          taskList.loading = false;
        }
      }
      function scheduleListPoll() {
        clearTimeout(listTimer);
        if (!taskList.open || document.hidden) return;
        const active = taskList.rows.some(r => r.status === 'pending' || r.status === 'processing');
        listTimer = setTimeout(async () => {
          await loadTaskList(false);
          scheduleListPoll();
        }, active ? POLL_ACTIVE_MS : POLL_IDLE_MS);
      }
      function pokeListPoll() {
        if (!taskList.open) return;
        loadTaskList(false).then(scheduleListPoll);
      }
      function toggleTaskList() {
        taskList.open = !taskList.open;
        if (taskList.open) pokeListPoll();
        else clearTimeout(listTimer);
      }
      function manualRefresh() {
        loadTaskList(true).then(scheduleListPoll);
      }
      watch(() => taskList.filter, () => { if (taskList.open) pokeListPoll(); });
      function onVisibility() {
        if (document.hidden) clearTimeout(listTimer);
        else pokeListPoll();
      }
      onMounted(() => document.addEventListener('visibilitychange', onVisibility));
      onBeforeUnmount(() => {
        document.removeEventListener('visibilitychange', onVisibility);
        clearTimeout(listTimer);
        finishPoll();
      });

      const filterOptions = computed(() => [
        { label: t('filter.all'), value: '' },
        { label: t('status.pending'), value: 'pending' },
        { label: t('status.processing'), value: 'processing' },
        { label: t('status.completed'), value: 'completed' },
        { label: t('status.failed'), value: 'failed' },
        { label: t('status.cancelled'), value: 'cancelled' },
      ]);

      // —— 历史任务查看 / 删除 ——
      const viewer = reactive({ show: false, loading: false, data: null, title: '' });
      async function viewTask(row) {
        viewer.title = row.wav_name || row.task_id;
        viewer.data = null;
        viewer.show = true;
        viewer.loading = true;
        try {
          const res = await fetch('/v2/tasks/' + row.task_id, { headers: authHeaders() });
          const data = await res.json();
          if (data.status === 'not_found') {
            viewer.show = false;
            message.warning(t('msg.taskExpired'));
            pokeListPoll();
            return;
          }
          viewer.data = data;
        } catch (e) {
          viewer.show = false;
          message.error(t('msg.loadFailed', e.message));
        } finally {
          viewer.loading = false;
        }
      }
      function confirmDelete(row) {
        dialog.warning({
          title: t('task.deleteTitle'),
          content: t('task.deleteConfirm', row.wav_name || row.task_id),
          positiveText: t('task.deletePositive'),
          negativeText: t('task.deleteNegative'),
          onPositiveClick: async () => {
            try {
              const res = await fetch('/v2/tasks/' + row.task_id, { method: 'DELETE', headers: authHeaders() });
              const data = await res.json();
              if (data.status === 'deleted' || data.status === 'cancelled') {
                message.success(data.message || t('msg.deleted'));
              } else if (data.status === 'not_found') {
                message.warning(t('msg.taskDeleted'));
              } else {
                message.info(data.message || data.status);
              }
            } catch (e) {
              message.error(t('msg.deleteFailed', e.message));
            }
            pokeListPoll();
          },
        });
      }

      // 英文表头更长（Processing/Progress 等）：列宽随语言取值，避免折行
      const wide = computed(() => locale.value === 'en');
      const columns = computed(() => [
        {
          title: t('col.file'),
          key: 'wav_name',
          ellipsis: { tooltip: true },
          render: row => row.wav_name || (row.task_id.substring(0, 8) + '...'),
        },
        {
          title: t('col.status'), key: 'status', width: wide.value ? 116 : 100,
          render: row => h(naive.NTag, { size: 'small', bordered: false, type: STATUS_TAG_TYPES[row.status] || 'default' },
            { default: () => statusLabel(row.status) }),
        },
        {
          title: t('col.progress'), key: 'progress', width: wide.value ? 96 : 80,
          render: row => Math.round((row.progress || 0) * 100) + '%',
        },
        { title: t('col.createdAt'), key: 'created_at', width: 170, render: row => fmtDate(row.created_at) },
        {
          title: t('col.actions'), key: 'actions', width: 140,
          render: row => h(naive.NSpace, { size: 'small' }, { default: () => [
            h(naive.NButton, { size: 'tiny', tertiary: true, onClick: e => { e.stopPropagation(); viewTask(row); } }, { default: () => t('col.view') }),
            h(naive.NButton, { size: 'tiny', tertiary: true, type: 'error', onClick: e => { e.stopPropagation(); confirmDelete(row); } }, { default: () => t('col.delete') }),
          ] }),
        },
      ]);
      const rowProps = row => ({ style: 'cursor: pointer;', onClick: () => viewTask(row) });

      return {
        uploadFileList, onUploadChange, fileSize, audioSrc, audioRef, selectedFile,
        canIdentify, identifySpeakers, srv, adv, ph, tagOnly, tagScene,
        scenePreset, scenePresets, scenePresetOptions,
        current, progressPct, submit, cancelTask, seekAudio,
        taskList, toggleTaskList, manualRefresh, filterOptions, columns, rowProps,
        viewer, t,
      };
    },
    template: `
      <div class="page-flex">
        <div class="workspace">
          <div class="side-col">
            <n-card :bordered="false" class="panel" size="small">
              <template #header><span class="panel-title"><a-icon name="upload" size="15"></a-icon>{{ t('upload.title') }}</span></template>
              <!-- 不设 :max="1"：达到 max 后 n-upload 会禁用触发器导致无法换文件；
                   替换语义由 onUploadChange 取末项实现（列表恒 ≤1） -->
              <n-upload :file-list="uploadFileList" :default-upload="false" :show-file-list="false"
                        accept=".wav,.mp3,.flac,.m4a,.aac,.ogg,.wma,.amr,.opus" @change="onUploadChange">
                <n-upload-dragger>
                  <div style="color:#14b8a6;margin-bottom:8px;"><a-icon name="upload" size="30"></a-icon></div>
                  <n-text style="font-size:.92em;font-weight:600;">{{ t('upload.hint') }}</n-text>
                  <n-p depth="3" style="font-size:.76em;margin:6px 0 0;">{{ t('upload.formats') }}</n-p>
                </n-upload-dragger>
              </n-upload>
              <template v-if="selectedFile">
                <div class="file-meta">
                  <a-icon name="file" size="14"></a-icon>
                  <span class="file-name" :title="selectedFile.name">{{ selectedFile.name }}</span>
                  <n-tag size="tiny" :bordered="false">{{ fileSize }}</n-tag>
                </div>
                <audio ref="audioRef" class="audio-box" controls :src="audioSrc"></audio>
                <n-checkbox v-if="srv.tagging" v-model:checked="tagOnly" size="small" style="margin-top:12px;">
                  {{ t('upload.tagOnly') }}
                </n-checkbox>
                <n-checkbox v-if="srv.tagging && tagOnly" v-model:checked="tagScene" size="small" style="margin-top:8px;margin-left:22px;">
                  {{ t('upload.tagScene') }}
                </n-checkbox>
                <div v-if="srv.tagging && scenePresetOptions.length" class="adv-field" style="margin-top:12px;">
                  <span class="lbl">{{ t('scene.preset') }}</span>
                  <n-select v-model:value="scenePreset" :options="scenePresetOptions" size="small" style="width:200px;"></n-select>
                </div>
                <n-checkbox v-if="canIdentify && !tagOnly" v-model:checked="identifySpeakers" size="small" :disabled="!adv.diarize" style="margin-top:12px;">
                  {{ t('upload.identify') }}
                </n-checkbox>
                <n-collapse v-if="!tagOnly" style="margin-top:12px;">
                  <n-collapse-item :title="t('adv.title')" name="adv">
                    <div class="adv-hint">{{ t('adv.hint') }}</div>
                    <template v-if="srv.punc || srv.align || srv.speaker">
                      <div class="sec-title">{{ t('adv.output') }}</div>
                      <div v-if="srv.punc" class="adv-field"><span class="lbl">{{ t('adv.punc') }}</span><n-switch v-model:value="adv.withPunc" size="small"></n-switch></div>
                      <div v-if="srv.align" class="adv-field"><span class="lbl">{{ t('adv.words') }}</span><n-switch v-model:value="adv.withWords" size="small"></n-switch></div>
                      <div v-if="srv.speaker" class="adv-field"><span class="lbl">{{ t('adv.diarize') }}</span><n-switch v-model:value="adv.diarize" size="small"></n-switch></div>
                    </template>
                    <div class="sec-title">{{ t('adv.tuning') }}</div>
                    <div class="adv-field"><span class="lbl">{{ t('adv.segment') }}</span><n-input-number v-model:value="adv.maxSegment" size="small" :min="1" :max="30" clearable :placeholder="ph('max_segment')" style="width:128px;"></n-input-number></div>
                    <template v-if="srv.speakerDb">
                      <div class="adv-field"><span class="lbl" :class="{ muted: !adv.diarize }">{{ t('adv.idThreshold') }}</span><n-input-number v-model:value="adv.idThreshold" size="small" :min="0" :max="1" :step="0.05" clearable :disabled="!adv.diarize" :placeholder="ph('speaker_id_threshold')" style="width:128px;"></n-input-number></div>
                      <div class="adv-field"><span class="lbl" :class="{ muted: !adv.diarize }">{{ t('adv.idMargin') }}</span><n-input-number v-model:value="adv.idMargin" size="small" :min="0" :max="1" :step="0.05" clearable :disabled="!adv.diarize" :placeholder="ph('speaker_id_margin')" style="width:128px;"></n-input-number></div>
                    </template>
                  </n-collapse-item>
                </n-collapse>
                <n-button type="primary" size="large" block strong style="margin-top:14px;"
                          :loading="current.phase === 'submitting'"
                          :disabled="current.phase === 'submitting' || current.phase === 'running'" @click="submit">
                  {{ current.phase === 'running' || current.phase === 'submitting'
                       ? (tagOnly ? t('action.tagging') : t('action.recognizing'))
                       : (tagOnly ? t('action.tag') : t('action.start')) }}
                </n-button>
              </template>
            </n-card>
          </div>

          <div class="main-col">
            <n-card :bordered="false" class="panel" content-class="panel-body" size="small">
              <template #header><span class="panel-title"><a-icon name="doc" size="15"></a-icon>{{ t('result.title') }}</span></template>
              <template #header-extra>
                <n-button v-if="current.phase === 'running' || current.phase === 'submitting'" size="small" type="error" tertiary
                          :disabled="current.cancelling || !current.taskId" @click="cancelTask">
                  {{ current.cancelling ? t('action.cancelling') : t('action.cancel') }}
                </n-button>
              </template>
              <template v-if="current.phase === 'submitting' || current.phase === 'running'">
                <n-progress type="line" :percentage="progressPct" :height="8" :border-radius="4" :show-indicator="false" processing></n-progress>
                <n-text depth="3" style="display:block;margin-top:8px;font-size:.84em;font-variant-numeric:tabular-nums;">
                  {{ current.phase === 'submitting' ? t('result.uploading') : t('result.progress', progressPct) }}
                </n-text>
                <n-skeleton text :repeat="3" style="margin-top:18px;"></n-skeleton>
              </template>
              <n-alert v-else-if="current.phase === 'error'" type="error" :show-icon="true" :title="t('result.failed')">{{ current.error }}</n-alert>
              <result-view v-else-if="current.phase === 'done' && current.data" :data="current.data" :on-seek="seekAudio"></result-view>
              <n-empty v-else :description="t('result.empty')" style="margin:48px 0;"></n-empty>
            </n-card>
          </div>
        </div>

        <n-card :bordered="false" class="panel dock-card" :class="{ open: taskList.open }" content-class="dock-content" size="small" style="margin-top:20px;">
          <n-space justify="space-between" align="center">
            <n-button text style="font-size:.95em;font-weight:600;" @click="toggleTaskList">
              <a-icon name="list" size="15" style="margin-right:7px;color:#14b8a6;"></a-icon>{{ t('task.title') }}
              <a-icon name="chev" size="13" :style="{ marginLeft: '7px', transition: 'transform .2s', transform: taskList.open ? 'rotate(180deg)' : 'none' }"></a-icon>
            </n-button>
            <n-space v-if="taskList.open" size="small" align="center">
              <n-select v-model:value="taskList.filter" :options="filterOptions" size="small" style="width:110px;"></n-select>
              <n-button size="small" tertiary :loading="taskList.loading" @click="manualRefresh">{{ t('task.refresh') }}</n-button>
            </n-space>
          </n-space>
          <div v-if="taskList.open" class="dock-body" style="margin-top:12px;">
            <n-data-table :columns="columns" :data="taskList.rows" :row-props="rowProps" :loading="taskList.loading && !taskList.loaded"
                          :row-key="row => row.task_id" :scroll-x="680" size="small"></n-data-table>
            <n-text v-if="taskList.loaded" depth="3" class="poll-note">
              {{ t('task.pollNote') }}
            </n-text>
          </div>
        </n-card>

        <n-modal v-model:show="viewer.show" preset="card" :title="t('viewer.title', viewer.title)" style="max-width:760px;">
          <n-spin :show="viewer.loading">
            <template v-if="viewer.data">
              <n-alert v-if="viewer.data.status === 'failed'" type="error" :show-icon="true" style="margin-bottom:10px;">{{ viewer.data.error || t('result.failed') }}</n-alert>
              <n-alert v-else-if="viewer.data.status === 'cancelled' && !(viewer.data.result && viewer.data.result.segments && viewer.data.result.segments.length)"
                       type="warning" :show-icon="true" style="margin-bottom:10px;">{{ viewer.data.error || t('msg.cancelledNoResult') }}</n-alert>
              <result-view v-if="viewer.data.result" :data="viewer.data"></result-view>
              <n-descriptions v-else :column="1" size="small" label-placement="left">
                <n-descriptions-item :label="t('viewer.taskId')">{{ viewer.data.task_id }}</n-descriptions-item>
                <n-descriptions-item :label="t('viewer.status')">{{ viewer.data.status }}</n-descriptions-item>
                <n-descriptions-item :label="t('viewer.progress')">{{ Math.round((viewer.data.progress || 0) * 100) }}%</n-descriptions-item>
              </n-descriptions>
            </template>
            <div v-else style="min-height:80px;"></div>
          </n-spin>
        </n-modal>
      </div>`,
  };

  mountApp(AppBody);
})();
