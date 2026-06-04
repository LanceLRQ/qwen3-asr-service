/* 离线转写页（Vue 3 + Naive UI，无构建 UMD）。
 * 端点：POST /v2/asr、GET/DELETE /v2/tasks/{id}、GET /v2/tasks?history=true
 * 任务列表自适应轮询：有 pending/processing 任务 3s，全终态 30s；后台标签页暂停。
 */
(function () {
  'use strict';
  const { createApp, ref, reactive, computed, watch, onMounted, onBeforeUnmount, h } = Vue;
  const { fmtTime, fmtDate, makeRoot } = window.AsrCommon;

  const STATUS_LABELS = { pending: '排队中', processing: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消' };
  const STATUS_TAG_TYPES = { pending: 'warning', processing: 'info', completed: 'success', failed: 'error', cancelled: 'default' };
  const POLL_ACTIVE_MS = 3000;   // 列表含进行中任务时的刷新间隔
  const POLL_IDLE_MS = 30000;    // 全终态时的低频刷新（外部客户端新建任务也能感知）

  /* 结果展示（分段 + 全文 + JSON），当前任务与历史查看弹窗复用；onSeek 仅当前任务传入 */
  const ResultView = {
    props: { data: { type: Object, required: true }, onSeek: { type: Function, default: null } },
    setup(props) {
      const result = computed(() => props.data.result || {});
      const segments = computed(() => result.value.segments || []);
      const metaTags = computed(() => {
        const r = result.value, tags = [];
        if (r.language) tags.push('语言: ' + r.language);
        if (r.align_enabled != null) tags.push('对齐: ' + (r.align_enabled ? '开启' : '关闭'));
        if (r.punc_enabled != null) tags.push('标点: ' + (r.punc_enabled ? '开启' : '关闭'));
        if (r.duration != null) tags.push('时长: ' + r.duration.toFixed(1) + 's');
        return tags;
      });
      const showJson = ref(false);
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
      return { result, segments, metaTags, showJson, jsonText, downloadJson, seek, fmtTime };
    },
    template: `
      <div>
        <n-space v-if="metaTags.length" size="small" style="margin-bottom:10px;">
          <n-tag v-for="t in metaTags" :key="t" size="small" :bordered="false">{{ t }}</n-tag>
        </n-space>
        <n-h3 prefix="bar" style="margin:6px 0;">分段结果</n-h3>
        <n-empty v-if="!segments.length" description="无分段数据" size="small" style="margin:12px 0;"></n-empty>
        <div v-for="(seg, i) in segments" :key="i" class="segment-card" :style="onSeek ? '' : 'cursor:default;'" @click="seek(seg)">
          <span class="segment-time">[{{ fmtTime(seg.start) }} - {{ fmtTime(seg.end) }}]</span>
          <span>{{ seg.text }}</span>
        </div>
        <n-h3 prefix="bar" style="margin:16px 0 6px;">完整文本</n-h3>
        <n-card size="small" :bordered="true"><div class="full-text">{{ result.full_text || '' }}</div></n-card>
        <n-h3 prefix="bar" style="margin:16px 0 6px;">原始数据</n-h3>
        <n-space size="small">
          <n-button size="small" tertiary @click="showJson = !showJson">{{ showJson ? '收起 JSON' : '展开 JSON' }}</n-button>
          <n-button size="small" tertiary type="primary" @click="downloadJson">下载 JSON</n-button>
        </n-space>
        <pre v-if="showJson" class="json-pre" style="margin-top:8px;">{{ jsonText }}</pre>
      </div>`,
  };

  const AppBody = {
    components: { 'result-view': ResultView },
    props: { themeMode: { type: String, required: true } },
    emits: ['cycle-theme'],
    setup(props, { emit }) {
      const message = naive.useMessage();
      const dialog = naive.useDialog();

      // —— API Key（localStorage 记忆）——
      const apiKey = ref(localStorage.getItem('asr_api_key') || '');
      watch(apiKey, v => localStorage.setItem('asr_api_key', v.trim()));
      const authHeaders = () => {
        const k = apiKey.value.trim();
        return k ? { Authorization: 'Bearer ' + k } : {};
      };

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
      const fileLabel = computed(() =>
        selectedFile.value ? `${selectedFile.value.name} (${(selectedFile.value.size / 1024 / 1024).toFixed(2)} MB)` : ''
      );

      // —— 当前任务 ——
      // phase: idle | submitting | running | done | error
      const current = reactive({ taskId: null, phase: 'idle', progress: 0, error: '', data: null, cancelling: false });
      let detailTimer = null;
      function resetCurrent() {
        clearInterval(detailTimer); detailTimer = null;
        Object.assign(current, { taskId: null, phase: 'idle', progress: 0, error: '', data: null, cancelling: false });
      }
      const progressPct = computed(() => Math.round((current.progress || 0) * 100));

      async function submit() {
        if (!selectedFile.value || current.phase === 'submitting' || current.phase === 'running') return;
        resetCurrent();
        current.phase = 'submitting';
        const form = new FormData();
        form.append('file', selectedFile.value);
        try {
          const res = await fetch('/v2/asr', { method: 'POST', body: form, headers: authHeaders() });
          if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: '上传失败' }));
            throw new Error(err.detail || '上传失败');
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

      function finishPoll() { clearInterval(detailTimer); detailTimer = null; }
      function startDetailPoll(taskId) {
        finishPoll();
        detailTimer = setInterval(async () => {
          try {
            const res = await fetch('/v2/tasks/' + taskId, { headers: authHeaders() });
            const data = await res.json();
            if (data.status === 'processing' || data.status === 'pending' || data.status === 'queued') {
              current.progress = data.progress || 0;
            } else if (data.status === 'completed') {
              finishPoll();
              current.progress = 1;
              current.phase = 'done';
              current.data = data;
            } else if (data.status === 'cancelled') {
              finishPoll();
              if (data.result && data.result.segments && data.result.segments.length) {
                current.phase = 'done';
                current.data = data;
                message.info('任务已取消，已显示部分结果');
              } else {
                current.phase = 'error';
                current.error = data.error || '任务已取消';
              }
            } else if (data.status === 'failed') {
              finishPoll();
              current.phase = 'error';
              current.error = data.error || '识别失败';
            } else if (data.status === 'not_found') {
              finishPoll();
              current.phase = 'error';
              current.error = '任务不存在';
            }
          } catch (e) {
            finishPoll();
            current.phase = 'error';
            current.error = '轮询失败: ' + e.message;
          }
        }, 1000);
      }

      async function cancelTask() {
        if (!current.taskId || current.cancelling) return;
        current.cancelling = true;
        try {
          await fetch('/v2/tasks/' + current.taskId, { method: 'DELETE', headers: authHeaders() });
        } catch (e) { /* 取消结果由详情轮询判定 */ }
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
          if (notify) message.error('任务列表加载失败: ' + e.message);
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

      const filterOptions = [
        { label: '全部', value: '' },
        { label: '排队中', value: 'pending' },
        { label: '处理中', value: 'processing' },
        { label: '已完成', value: 'completed' },
        { label: '失败', value: 'failed' },
        { label: '已取消', value: 'cancelled' },
      ];

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
            message.warning('任务不存在或已过期');
            pokeListPoll();
            return;
          }
          viewer.data = data;
        } catch (e) {
          viewer.show = false;
          message.error('加载失败: ' + e.message);
        } finally {
          viewer.loading = false;
        }
      }
      function confirmDelete(row) {
        dialog.warning({
          title: '删除任务记录',
          content: `确定删除任务 ${row.wav_name || row.task_id} 吗？进行中的任务将被取消，历史记录将被删除。`,
          positiveText: '删除',
          negativeText: '取消',
          onPositiveClick: async () => {
            try {
              const res = await fetch('/v2/tasks/' + row.task_id, { method: 'DELETE', headers: authHeaders() });
              const data = await res.json();
              if (data.status === 'deleted' || data.status === 'cancelled') {
                message.success(data.message || '已删除');
              } else if (data.status === 'not_found') {
                message.warning('任务不存在或已被删除');
              } else {
                message.info(data.message || data.status);
              }
            } catch (e) {
              message.error('删除失败: ' + e.message);
            }
            pokeListPoll();
          },
        });
      }

      const columns = [
        {
          title: '文件 / 任务',
          key: 'wav_name',
          ellipsis: { tooltip: true },
          render: row => row.wav_name || (row.task_id.substring(0, 8) + '...'),
        },
        {
          title: '状态', key: 'status', width: 100,
          render: row => h(naive.NTag, { size: 'small', bordered: false, type: STATUS_TAG_TYPES[row.status] || 'default' },
            { default: () => STATUS_LABELS[row.status] || row.status }),
        },
        {
          title: '进度', key: 'progress', width: 80,
          render: row => Math.round((row.progress || 0) * 100) + '%',
        },
        { title: '创建时间', key: 'created_at', width: 170, render: row => fmtDate(row.created_at) },
        {
          title: '操作', key: 'actions', width: 140,
          render: row => h(naive.NSpace, { size: 'small' }, { default: () => [
            h(naive.NButton, { size: 'tiny', tertiary: true, onClick: e => { e.stopPropagation(); viewTask(row); } }, { default: () => '查看' }),
            h(naive.NButton, { size: 'tiny', tertiary: true, type: 'error', onClick: e => { e.stopPropagation(); confirmDelete(row); } }, { default: () => '删除' }),
          ] }),
        },
      ];
      const rowProps = row => ({ style: 'cursor: pointer;', onClick: () => viewTask(row) });

      const themeLabel = computed(() => ({ auto: '🌗 跟随系统', light: '☀️ 浅色', dark: '🌙 深色' }[props.themeMode]));

      return {
        apiKey, themeLabel,
        uploadFileList, onUploadChange, fileLabel, audioSrc, audioRef, selectedFile,
        current, progressPct, submit, cancelTask, seekAudio,
        taskList, toggleTaskList, manualRefresh, filterOptions, columns, rowProps,
        viewer,
        cycleTheme: () => emit('cycle-theme'),
      };
    },
    template: `
      <div>
        <n-space justify="center" align="center" style="margin-bottom:16px;">
          <n-input v-model:value="apiKey" type="password" show-password-on="click" placeholder="API Key（留空表示无需认证）" size="small" style="width:280px;"></n-input>
          <n-button size="small" quaternary @click="cycleTheme">{{ themeLabel }}</n-button>
        </n-space>

        <div class="page-body">
          <div class="side-col">
            <n-card title="📁 上传音频" size="small">
              <n-upload :file-list="uploadFileList" :default-upload="false" :max="1" :show-file-list="false"
                        accept=".wav,.mp3,.flac,.m4a,.aac,.ogg,.wma,.amr,.opus" @change="onUploadChange">
                <n-upload-dragger>
                  <div style="font-size:2em;">💾</div>
                  <n-text style="font-size:0.95em;font-weight:600;">点击或拖拽上传音频文件</n-text>
                  <n-p depth="3" style="font-size:0.8em;margin:6px 0 0;">支持 wav / mp3 / flac / m4a / aac / ogg / wma / amr / opus</n-p>
                </n-upload-dragger>
              </n-upload>
              <template v-if="selectedFile">
                <n-text depth="2" style="display:block;margin-top:10px;font-size:0.88em;">{{ fileLabel }}</n-text>
                <audio ref="audioRef" class="audio-box" controls :src="audioSrc"></audio>
                <n-button type="primary" block style="margin-top:12px;" :disabled="current.phase === 'submitting' || current.phase === 'running'" @click="submit">
                  {{ current.phase === 'running' || current.phase === 'submitting' ? '识别中...' : '开始识别' }}
                </n-button>
              </template>
            </n-card>
          </div>

          <div class="main-col">
            <n-card size="small" :title="current.phase === 'running' || current.phase === 'submitting' ? '🔄 识别中...' : '📊 识别结果'">
              <template v-if="current.phase === 'submitting' || current.phase === 'running'">
                <n-progress type="line" :percentage="progressPct" :indicator-placement="'inside'" processing></n-progress>
                <n-space justify="space-between" align="center" style="margin-top:10px;">
                  <n-text depth="3" style="font-size:0.88em;">{{ current.phase === 'submitting' ? '上传中...' : '识别中... ' + progressPct + '%' }}</n-text>
                  <n-button size="small" type="error" tertiary :disabled="current.cancelling || !current.taskId" @click="cancelTask">
                    {{ current.cancelling ? '取消中...' : '取消识别' }}
                  </n-button>
                </n-space>
              </template>
              <n-alert v-else-if="current.phase === 'error'" type="error" :show-icon="true" title="识别失败">{{ current.error }}</n-alert>
              <result-view v-else-if="current.phase === 'done' && current.data" :data="current.data" :on-seek="seekAudio"></result-view>
              <n-empty v-else description="上传音频并开始识别" style="margin:32px 0;"></n-empty>
            </n-card>
          </div>
        </div>

        <n-card size="small" style="margin-top:16px;">
          <n-space justify="space-between" align="center">
            <n-button text style="font-size:1em;font-weight:600;" @click="toggleTaskList">
              📋 任务列表 {{ taskList.open ? '▲' : '▼' }}
            </n-button>
            <n-space v-if="taskList.open" size="small" align="center">
              <n-select v-model:value="taskList.filter" :options="filterOptions" size="small" style="width:110px;"></n-select>
              <n-button size="small" tertiary :loading="taskList.loading" @click="manualRefresh">刷新</n-button>
            </n-space>
          </n-space>
          <template v-if="taskList.open">
            <n-data-table :columns="columns" :data="taskList.rows" :row-props="rowProps" :loading="taskList.loading && !taskList.loaded"
                          :row-key="row => row.task_id" size="small" style="margin-top:12px;"></n-data-table>
            <n-text v-if="taskList.loaded" depth="3" style="display:block;margin-top:8px;font-size:0.78em;">
              列表自动刷新：进行中任务每 3 秒，空闲每 30 秒；含持久化历史记录
            </n-text>
          </template>
        </n-card>

        <n-modal v-model:show="viewer.show" preset="card" :title="'任务详情：' + viewer.title" style="max-width:760px;">
          <n-spin :show="viewer.loading">
            <template v-if="viewer.data">
              <n-alert v-if="viewer.data.status === 'failed'" type="error" :show-icon="true" style="margin-bottom:10px;">{{ viewer.data.error || '识别失败' }}</n-alert>
              <n-alert v-else-if="viewer.data.status === 'cancelled' && !(viewer.data.result && viewer.data.result.segments && viewer.data.result.segments.length)"
                       type="warning" :show-icon="true" style="margin-bottom:10px;">{{ viewer.data.error || '任务已取消，无结果' }}</n-alert>
              <result-view v-if="viewer.data.result" :data="viewer.data"></result-view>
              <n-descriptions v-else :column="1" size="small" label-placement="left">
                <n-descriptions-item label="任务 ID">{{ viewer.data.task_id }}</n-descriptions-item>
                <n-descriptions-item label="状态">{{ viewer.data.status }}</n-descriptions-item>
                <n-descriptions-item label="进度">{{ Math.round((viewer.data.progress || 0) * 100) }}%</n-descriptions-item>
              </n-descriptions>
            </template>
            <div v-else style="min-height:80px;"></div>
          </n-spin>
        </n-modal>
      </div>`,
  };

  createApp(makeRoot(AppBody)).use(naive).mount('#app');
})();
