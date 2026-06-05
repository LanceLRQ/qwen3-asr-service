/* 离线转写页（Vue 3 + Naive UI，无构建 UMD）。
 * 端点：POST /v2/asr、GET/DELETE /v2/tasks/{id}、GET /v2/tasks?history=true
 * 任务列表自适应轮询：有 pending/processing 任务 3s，全终态 30s；后台标签页暂停。
 */
(function () {
  'use strict';
  const { ref, reactive, computed, watch, onMounted, onBeforeUnmount, h } = Vue;
  const { fmtTime, fmtDate, fmtBytes, authHeaders, mountApp } = window.AsrCommon;

  const STATUS_LABELS = { pending: '排队中', processing: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消' };
  const STATUS_TAG_TYPES = { pending: 'warning', processing: 'info', completed: 'success', failed: 'error', cancelled: 'default' };
  const POLL_ACTIVE_MS = 3000;   // 列表含进行中任务时的刷新间隔
  const POLL_IDLE_MS = 30000;    // 全终态时的低频刷新（外部客户端新建任务也能感知）

  /* 结果展示（分段时间轴 + 全文/JSON 折叠），当前任务与历史查看弹窗复用；onSeek 仅当前任务传入 */
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
      return { result, segments, metaTags, jsonText, downloadJson, seek, fmtTime };
    },
    template: `
      <div>
        <n-space v-if="metaTags.length" size="small" style="margin-bottom:14px;">
          <n-tag v-for="t in metaTags" :key="t" size="small" :bordered="false">{{ t }}</n-tag>
        </n-space>
        <div class="sec-title">分段结果</div>
        <n-empty v-if="!segments.length" description="无分段数据" size="small" style="margin:12px 0;"></n-empty>
        <div v-else>
          <div v-for="(seg, i) in segments" :key="i" class="seg-row" :class="{ static: !onSeek }" @click="seek(seg)">
            <span class="seg-time">{{ fmtTime(seg.start) }}</span>
            <span class="seg-text">{{ seg.text }}</span>
            <span v-if="onSeek" class="seg-play"><a-icon name="play" size="14"></a-icon></span>
          </div>
        </div>
        <n-collapse :default-expanded-names="['full']" style="margin-top:18px;">
          <n-collapse-item title="完整文本" name="full">
            <div class="full-text">{{ result.full_text || '' }}</div>
          </n-collapse-item>
          <n-collapse-item title="原始 JSON" name="json">
            <template #header-extra>
              <n-button size="tiny" tertiary @click.stop="downloadJson">下载 JSON</n-button>
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
                message.info('任务已取消，已显示部分结果');
              } else {
                current.phase = 'error';
                current.error = data.error || '任务已取消';
              }
            } else if (data.status === 'failed') {
              current.phase = 'error';
              current.error = data.error || '识别失败';
            } else if (data.status === 'not_found') {
              current.phase = 'error';
              current.error = '任务不存在';
            } else {
              // 未知状态（如鉴权失效后的非任务响应体）也终止，避免静默无限轮询
              current.phase = 'error';
              current.error = '未知任务状态: ' + (data.status || '(空)');
            }
          } catch (e) {
            current.phase = 'error';
            current.error = '轮询失败: ' + e.message;
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
          message.error('取消请求发送失败: ' + e.message);
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

      return {
        uploadFileList, onUploadChange, fileSize, audioSrc, audioRef, selectedFile,
        current, progressPct, submit, cancelTask, seekAudio,
        taskList, toggleTaskList, manualRefresh, filterOptions, columns, rowProps,
        viewer,
      };
    },
    template: `
      <div class="page-flex">
        <div class="workspace">
          <div class="side-col">
            <n-card :bordered="false" class="panel" size="small">
              <template #header><span class="panel-title"><a-icon name="upload" size="15"></a-icon>上传音频</span></template>
              <n-upload :file-list="uploadFileList" :default-upload="false" :max="1" :show-file-list="false"
                        accept=".wav,.mp3,.flac,.m4a,.aac,.ogg,.wma,.amr,.opus" @change="onUploadChange">
                <n-upload-dragger>
                  <div style="color:#14b8a6;margin-bottom:8px;"><a-icon name="upload" size="30"></a-icon></div>
                  <n-text style="font-size:.92em;font-weight:600;">点击或拖拽上传音频文件</n-text>
                  <n-p depth="3" style="font-size:.76em;margin:6px 0 0;">wav / mp3 / flac / m4a / aac / ogg / wma / amr / opus</n-p>
                </n-upload-dragger>
              </n-upload>
              <template v-if="selectedFile">
                <div class="file-meta">
                  <a-icon name="file" size="14"></a-icon>
                  <span class="file-name" :title="selectedFile.name">{{ selectedFile.name }}</span>
                  <n-tag size="tiny" :bordered="false">{{ fileSize }}</n-tag>
                </div>
                <audio ref="audioRef" class="audio-box" controls :src="audioSrc"></audio>
                <n-button type="primary" size="large" block strong style="margin-top:14px;"
                          :loading="current.phase === 'submitting'"
                          :disabled="current.phase === 'submitting' || current.phase === 'running'" @click="submit">
                  {{ current.phase === 'running' || current.phase === 'submitting' ? '识别中…' : '开始识别' }}
                </n-button>
              </template>
            </n-card>
          </div>

          <div class="main-col">
            <n-card :bordered="false" class="panel" content-class="panel-body" size="small">
              <template #header><span class="panel-title"><a-icon name="doc" size="15"></a-icon>识别结果</span></template>
              <template #header-extra>
                <n-button v-if="current.phase === 'running' || current.phase === 'submitting'" size="small" type="error" tertiary
                          :disabled="current.cancelling || !current.taskId" @click="cancelTask">
                  {{ current.cancelling ? '取消中…' : '取消识别' }}
                </n-button>
              </template>
              <template v-if="current.phase === 'submitting' || current.phase === 'running'">
                <n-progress type="line" :percentage="progressPct" :height="8" :border-radius="4" :show-indicator="false" processing></n-progress>
                <n-text depth="3" style="display:block;margin-top:8px;font-size:.84em;font-variant-numeric:tabular-nums;">
                  {{ current.phase === 'submitting' ? '上传中…' : '识别中 ' + progressPct + '%' }}
                </n-text>
                <n-skeleton text :repeat="3" style="margin-top:18px;"></n-skeleton>
              </template>
              <n-alert v-else-if="current.phase === 'error'" type="error" :show-icon="true" title="识别失败">{{ current.error }}</n-alert>
              <result-view v-else-if="current.phase === 'done' && current.data" :data="current.data" :on-seek="seekAudio"></result-view>
              <n-empty v-else description="上传音频并开始识别" style="margin:48px 0;"></n-empty>
            </n-card>
          </div>
        </div>

        <n-card :bordered="false" class="panel dock-card" :class="{ open: taskList.open }" content-class="dock-content" size="small" style="margin-top:20px;">
          <n-space justify="space-between" align="center">
            <n-button text style="font-size:.95em;font-weight:600;" @click="toggleTaskList">
              <a-icon name="list" size="15" style="margin-right:7px;color:#14b8a6;"></a-icon>任务历史
              <a-icon name="chev" size="13" :style="{ marginLeft: '7px', transition: 'transform .2s', transform: taskList.open ? 'rotate(180deg)' : 'none' }"></a-icon>
            </n-button>
            <n-space v-if="taskList.open" size="small" align="center">
              <n-select v-model:value="taskList.filter" :options="filterOptions" size="small" style="width:110px;"></n-select>
              <n-button size="small" tertiary :loading="taskList.loading" @click="manualRefresh">刷新</n-button>
            </n-space>
          </n-space>
          <div v-if="taskList.open" class="dock-body" style="margin-top:12px;">
            <n-data-table :columns="columns" :data="taskList.rows" :row-props="rowProps" :loading="taskList.loading && !taskList.loaded"
                          :row-key="row => row.task_id" :scroll-x="680" size="small"></n-data-table>
            <n-text v-if="taskList.loaded" depth="3" class="poll-note">
              列表自动刷新：进行中任务每 3 秒，空闲每 30 秒；含持久化历史记录
            </n-text>
          </div>
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

  mountApp(AppBody);
})();
