HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Qwen3-ASR Service</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; color: #333; min-height: 100vh; }
.container { max-width: 800px; margin: 0 auto; padding: 24px 16px; }
h1 { text-align: center; margin-bottom: 24px; color: #1a1a2e; font-size: 1.6em; }

/* 上传区域 */
.upload-zone {
  border: 2px dashed #cbd5e1; border-radius: 12px; padding: 40px 20px;
  text-align: center; cursor: pointer; transition: all .2s;
  background: #fff; margin-bottom: 20px;
}
.upload-zone:hover, .upload-zone.dragover { border-color: #6366f1; background: #eef2ff; }
.upload-zone p { color: #64748b; margin-top: 8px; font-size: 0.9em; }
.upload-zone .icon { font-size: 2.5em; margin-bottom: 8px; }
.upload-zone .btn-text { font-size: 1.1em; color: #6366f1; font-weight: 600; }

/* 文件信息 & 音频播放器 */
.file-info {
  background: #fff; border-radius: 10px; padding: 16px; margin-bottom: 20px;
  display: none; box-shadow: 0 1px 3px rgba(0,0,0,.08);
}
.file-info .name { font-weight: 600; margin-bottom: 8px; word-break: break-all; }
.file-info audio { width: 100%; margin-top: 8px; }

/* 提交按钮 */
.submit-btn {
  width: 100%; padding: 12px; border: none; border-radius: 8px;
  background: #6366f1; color: #fff; font-size: 1em; font-weight: 600;
  cursor: pointer; transition: background .2s; margin-bottom: 20px; display: none;
}
.submit-btn:hover { background: #4f46e5; }
.submit-btn:disabled { background: #a5b4fc; cursor: not-allowed; }

/* 进度条 */
.progress-wrap {
  background: #fff; border-radius: 10px; padding: 16px; margin-bottom: 20px;
  display: none; box-shadow: 0 1px 3px rgba(0,0,0,.08);
}
.progress-bar-bg {
  background: #e2e8f0; border-radius: 6px; height: 12px; overflow: hidden;
}
.progress-bar {
  background: linear-gradient(90deg, #6366f1, #818cf8); height: 100%;
  border-radius: 6px; width: 0%; transition: width .3s;
}
.progress-text { text-align: center; margin-top: 8px; font-size: 0.9em; color: #64748b; }

/* 错误提示 */
.error-box {
  background: #fef2f2; border: 1px solid #fecaca; border-radius: 10px;
  padding: 16px; margin-bottom: 20px; display: none; color: #dc2626;
}

/* 结果区域 */
.result-area { display: none; }
.result-section {
  background: #fff; border-radius: 10px; padding: 16px; margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08);
}
.result-section h3 { font-size: 1em; margin-bottom: 12px; color: #374151; }

/* 元信息 */
.meta-tags { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }
.meta-tag {
  background: #f1f5f9; border-radius: 6px; padding: 4px 10px;
  font-size: 0.82em; color: #475569;
}

/* 分段卡片 */
.segment-card {
  border-left: 3px solid #6366f1; padding: 8px 12px; margin-bottom: 8px;
  background: #f8fafc; border-radius: 0 6px 6px 0; cursor: pointer;
  transition: background .15s;
}
.segment-card:hover { background: #eef2ff; }
.segment-time { font-size: 0.8em; color: #6366f1; font-weight: 600; font-family: monospace; }
.segment-text { margin-top: 4px; line-height: 1.5; }

/* 完整文本 */
.full-text-box {
  background: #f8fafc; border-radius: 8px; padding: 12px;
  line-height: 1.7; white-space: pre-wrap; word-break: break-all;
}

/* JSON 折叠 */
.json-toggle {
  background: none; border: 1px solid #cbd5e1; border-radius: 6px;
  padding: 6px 14px; cursor: pointer; font-size: 0.85em; color: #475569;
}
.json-content {
  display: none; margin-top: 10px; background: #1e293b; color: #e2e8f0;
  border-radius: 8px; padding: 12px; font-family: monospace; font-size: 0.82em;
  white-space: pre-wrap; word-break: break-all; max-height: 400px; overflow-y: auto;
}

/* 下载按钮 */
.download-btn {
  background: #10b981; color: #fff; border: none; border-radius: 6px;
  padding: 8px 16px; cursor: pointer; font-size: 0.85em; margin-left: 8px;
}
.download-btn:hover { background: #059669; }
</style>
</head>
<body>
<div class="container">
  <h1>Qwen3-ASR Service</h1>

  <!-- API Key（可选） -->
  <div style="background:#fff;border-radius:10px;padding:12px 16px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.08);display:flex;align-items:center;gap:10px;">
    <label for="apiKeyInput" style="font-size:0.9em;color:#475569;white-space:nowrap;">API Key</label>
    <input type="password" id="apiKeyInput" placeholder="留空表示无需认证" style="flex:1;border:1px solid #cbd5e1;border-radius:6px;padding:6px 10px;font-size:0.9em;outline:none;">
  </div>

  <!-- 上传区域 -->
  <div class="upload-zone" id="uploadZone">
    <div class="icon">&#128190;</div>
    <div class="btn-text">点击或拖拽上传音频文件</div>
    <p>支持格式：wav, mp3, flac, m4a, aac, ogg, wma, amr, opus</p>
    <input type="file" id="fileInput" accept=".wav,.mp3,.flac,.m4a,.aac,.ogg,.wma,.amr,.opus" hidden>
  </div>

  <!-- 文件信息 & 播放器 -->
  <div class="file-info" id="fileInfo">
    <div class="name" id="fileName"></div>
    <audio id="audioPlayer" controls></audio>
  </div>

  <!-- 提交按钮 -->
  <button class="submit-btn" id="submitBtn">开始识别</button>

  <!-- 进度条 -->
  <div class="progress-wrap" id="progressWrap">
    <div class="progress-bar-bg"><div class="progress-bar" id="progressBar"></div></div>
    <div class="progress-text" id="progressText">准备中...</div>
  </div>

  <!-- 错误提示 -->
  <div class="error-box" id="errorBox"></div>

  <!-- 结果区域 -->
  <div class="result-area" id="resultArea">
    <!-- 元信息 -->
    <div class="result-section">
      <h3>识别信息</h3>
      <div class="meta-tags" id="metaTags"></div>
    </div>

    <!-- 分段结果 -->
    <div class="result-section">
      <h3>分段结果</h3>
      <div id="segments"></div>
    </div>

    <!-- 完整文本 -->
    <div class="result-section">
      <h3>完整文本</h3>
      <div class="full-text-box" id="fullText"></div>
    </div>

    <!-- JSON -->
    <div class="result-section">
      <h3>原始数据</h3>
      <button class="json-toggle" id="jsonToggle">展开 JSON</button>
      <button class="download-btn" id="downloadBtn">下载 JSON</button>
      <div class="json-content" id="jsonContent"></div>
    </div>
  </div>
</div>

<script>
(function() {
  const $ = id => document.getElementById(id);
  const uploadZone = $('uploadZone'), fileInput = $('fileInput');
  const fileInfo = $('fileInfo'), fileName = $('fileName'), audioPlayer = $('audioPlayer');
  const submitBtn = $('submitBtn'), progressWrap = $('progressWrap');
  const progressBar = $('progressBar'), progressText = $('progressText');
  const errorBox = $('errorBox'), resultArea = $('resultArea');
  const metaTags = $('metaTags'), segments = $('segments'), fullText = $('fullText');
  const jsonToggle = $('jsonToggle'), jsonContent = $('jsonContent'), downloadBtn = $('downloadBtn');

  const apiKeyInput = $('apiKeyInput');
  let selectedFile = null;
  let audioObjectURL = null;
  let pollTimer = null;
  let resultData = null;

  function authHeaders() {
    const key = apiKeyInput.value.trim();
    return key ? { 'Authorization': 'Bearer ' + key } : {};
  }

  // 格式化时间 (秒 -> mm:ss.xx)
  function fmtTime(s) {
    if (s == null) return '--:--.--';
    const m = Math.floor(s / 60);
    const sec = s - m * 60;
    return String(m).padStart(2, '0') + ':' + sec.toFixed(2).padStart(5, '0');
  }

  // 拖拽
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
  uploadZone.addEventListener('drop', e => {
    e.preventDefault(); uploadZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
  });
  uploadZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => { if (fileInput.files.length) handleFile(fileInput.files[0]); });

  function handleFile(file) {
    selectedFile = file;
    fileName.textContent = file.name + ' (' + (file.size / 1024 / 1024).toFixed(2) + ' MB)';
    fileInfo.style.display = 'block';
    submitBtn.style.display = 'block';
    submitBtn.disabled = false;
    // 音频播放
    if (audioObjectURL) URL.revokeObjectURL(audioObjectURL);
    audioObjectURL = URL.createObjectURL(file);
    audioPlayer.src = audioObjectURL;
    // 重置状态
    progressWrap.style.display = 'none';
    errorBox.style.display = 'none';
    resultArea.style.display = 'none';
  }

  // 提交
  submitBtn.addEventListener('click', async () => {
    if (!selectedFile) return;
    submitBtn.disabled = true;
    errorBox.style.display = 'none';
    resultArea.style.display = 'none';
    progressWrap.style.display = 'block';
    progressBar.style.width = '0%';
    progressText.textContent = '上传中...';

    const form = new FormData();
    form.append('file', selectedFile);

    try {
      const res = await fetch('/v1/asr', { method: 'POST', body: form, headers: authHeaders() });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '上传失败' }));
        throw new Error(err.detail || '上传失败');
      }
      const data = await res.json();
      startPolling(data.task_id);
    } catch (e) {
      showError(e.message);
    }
  });

  function startPolling(taskId) {
    progressText.textContent = '识别中...';
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch('/v1/asr/' + taskId, { headers: authHeaders() });
        const data = await res.json();
        if (data.status === 'processing' || data.status === 'queued') {
          const pct = Math.round((data.progress || 0) * 100);
          progressBar.style.width = pct + '%';
          progressText.textContent = '识别中... ' + pct + '%';
        } else if (data.status === 'completed') {
          clearInterval(pollTimer); pollTimer = null;
          progressBar.style.width = '100%';
          progressText.textContent = '识别完成';
          showResult(data);
        } else if (data.status === 'failed') {
          clearInterval(pollTimer); pollTimer = null;
          showError(data.error || '识别失败');
        } else if (data.status === 'not_found') {
          clearInterval(pollTimer); pollTimer = null;
          showError('任务不存在');
        }
      } catch (e) {
        clearInterval(pollTimer); pollTimer = null;
        showError('轮询失败: ' + e.message);
      }
    }, 1000);
  }

  function showError(msg) {
    progressWrap.style.display = 'none';
    errorBox.textContent = msg;
    errorBox.style.display = 'block';
    submitBtn.disabled = false;
  }

  function showResult(data) {
    resultData = data;
    const r = data.result || {};

    // 元信息
    metaTags.innerHTML = '';
    const tags = [];
    if (r.language) tags.push('语言: ' + r.language);
    if (r.align_enabled != null) tags.push('对齐: ' + (r.align_enabled ? '开启' : '关闭'));
    if (r.punc_enabled != null) tags.push('标点: ' + (r.punc_enabled ? '开启' : '关闭'));
    if (r.duration != null) tags.push('时长: ' + r.duration.toFixed(1) + 's');
    tags.forEach(t => {
      const span = document.createElement('span');
      span.className = 'meta-tag';
      span.textContent = t;
      metaTags.appendChild(span);
    });

    // 分段
    segments.innerHTML = '';
    (r.segments || []).forEach(seg => {
      const card = document.createElement('div');
      card.className = 'segment-card';
      card.innerHTML = '<div class="segment-time">[' + fmtTime(seg.start) + ' - ' + fmtTime(seg.end) + ']</div>'
        + '<div class="segment-text">' + escapeHtml(seg.text) + '</div>';
      card.addEventListener('click', () => {
        if (seg.start != null) { audioPlayer.currentTime = seg.start; audioPlayer.play(); }
      });
      segments.appendChild(card);
    });

    // 完整文本
    fullText.textContent = r.full_text || '';

    // JSON
    jsonContent.textContent = JSON.stringify(data, null, 2);
    jsonContent.style.display = 'none';
    jsonToggle.textContent = '展开 JSON';

    resultArea.style.display = 'block';
    submitBtn.disabled = false;
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.appendChild(document.createTextNode(s || ''));
    return d.innerHTML;
  }

  // JSON 折叠
  jsonToggle.addEventListener('click', () => {
    const open = jsonContent.style.display === 'none';
    jsonContent.style.display = open ? 'block' : 'none';
    jsonToggle.textContent = open ? '收起 JSON' : '展开 JSON';
  });

  // 下载 JSON
  downloadBtn.addEventListener('click', () => {
    if (!resultData) return;
    const blob = new Blob([JSON.stringify(resultData, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'asr_result.json';
    a.click();
    URL.revokeObjectURL(a.href);
  });
})();
</script>
</body>
</html>
"""
