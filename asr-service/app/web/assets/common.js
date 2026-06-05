/* Web UI 公共模块（无构建 UMD）：应用栏根组件工厂 + 共享 API Key + 内联图标 + 时间格式化。
 * 依赖全局 Vue / naive（vendor 脚本先加载）。
 */
window.AsrCommon = (function () {
  'use strict';
  const { ref, reactive, computed, watch, onMounted, h, createApp } = Vue;

  /* 秒 → mm:ss.ss（分段时间戳） */
  function fmtTime(s) {
    if (s == null) return '--:--.--';
    const m = Math.floor(s / 60);
    const sec = s - m * 60;
    return String(m).padStart(2, '0') + ':' + sec.toFixed(2).padStart(5, '0');
  }

  /* 毫秒 → mm:ss.ss（实时 final 时间戳） */
  function fmtMs(ms) {
    return ms == null ? '--:--.--' : fmtTime(ms / 1000);
  }

  /* ISO 时间 → "YYYY-MM-DD HH:MM:SS" */
  function fmtDate(iso) {
    return iso ? iso.replace('T', ' ').substring(0, 19) : '--';
  }

  /* 字节数 → "x.xx MB"（文件大小展示） */
  function fmtBytes(n) {
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }

  /* 说话人徽标取色：标签字母 → 固定 8 色板下标（app.css .spk-0 ~ .spk-7，同标签恒同色） */
  function spkIdx(label) { return ((label.charCodeAt(0) - 65) % 8 + 8) % 8; }

  /* —— 共享 API Key（两页共用 localStorage 键 asr_api_key，应用栏 popover 中编辑）—— */
  const apiKey = ref(localStorage.getItem('asr_api_key') || '');
  watch(apiKey, v => localStorage.setItem('asr_api_key', v.trim()));
  function authHeaders() {
    const k = apiKey.value.trim();
    return k ? { Authorization: 'Bearer ' + k } : {};
  }

  /* —— 内联 SVG 图标（feather 风格 stroke 路径，'|' 分隔多条 path）—— */
  const ICONS = {
    logo: 'M3 10v4|M7 7v10|M11 3v18|M15 8v8|M19 6v12',
    upload: 'M12 16V4|M8 8l4-4 4 4|M4 20h16',
    download: 'M12 4v12|M8 12l4 4 4-4|M4 20h16',
    play: 'M6 4l14 8-14 8V4z',
    stop: 'M7 7h10v10H7z',
    mic: 'M12 2a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z|M19 11a7 7 0 0 1-14 0|M12 18v4|M8 22h8',
    file: 'M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z|M13 2v7h7',
    doc: 'M4 6h16|M4 12h16|M4 18h10',
    list: 'M8 6h13|M8 12h13|M8 18h13|M3.5 6h.01|M3.5 12h.01|M3.5 18h.01',
    refresh: 'M23 4v6h-6|M20.49 15a9 9 0 1 1-2.12-9.36L23 10',
    key: 'M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4',
    sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z|M12 1v2|M12 21v2|M4.22 4.22l1.42 1.42|M18.36 18.36l1.42 1.42|M1 12h2|M21 12h2|M4.22 19.78l1.42-1.42|M18.36 5.64l1.42-1.42',
    moon: 'M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z',
    auto: 'M2 5h20v12H2z|M8 21h8|M12 17v4',
    chev: 'M6 9l6 6 6-6',
  };
  const AIcon = {
    name: 'AIcon',
    props: { name: { type: String, required: true }, size: { type: [Number, String], default: 16 } },
    setup(props) {
      return () => h('svg', {
        viewBox: '0 0 24 24', width: props.size, height: props.size,
        fill: 'none', stroke: 'currentColor', 'stroke-width': 2,
        'stroke-linecap': 'round', 'stroke-linejoin': 'round',
        style: 'flex-shrink:0;vertical-align:-2px;',
      }, (ICONS[props.name] || '').split('|').map(d => h('path', { d })));
    },
  };

  /* 构造页面根组件：主题（默认亮色 + 手动循环，localStorage 记忆）+ 应用栏状态
   * （服务状态点 / API Key popover / 主题按钮），in-DOM 根模板直接使用其返回值。
   * AppBody 可省略（如文档页：正文为服务端渲染 HTML，全部内容都在 in-DOM 模板里）。 */
  function makeRoot(AppBody) {
    return {
      components: AppBody ? { 'app-body': AppBody } : {},
      setup() {
        const osTheme = naive.useOsTheme();
        const themeMode = ref(localStorage.getItem('asr_theme') || 'light'); // light | dark | auto，默认亮色
        const isDark = computed(() => (themeMode.value === 'auto' ? osTheme.value === 'dark' : themeMode.value === 'dark'));
        const theme = computed(() => (isDark.value ? naive.darkTheme : null));
        watch(isDark, v => document.body.classList.toggle('dark', v), { immediate: true });
        const themeOverrides = computed(() => ({
          common: {
            primaryColor: '#14b8a6', primaryColorHover: '#0d9488', primaryColorPressed: '#0f766e', primaryColorSuppl: '#14b8a6',
            borderRadius: '8px',
            bodyColor: isDark.value ? '#0e0f13' : '#f5f6f8',
          },
        }));
        function cycleTheme() {
          const order = ['auto', 'light', 'dark'];
          themeMode.value = order[(order.indexOf(themeMode.value) + 1) % order.length];
          localStorage.setItem('asr_theme', themeMode.value);
        }
        const themeIcon = computed(() => ({ auto: 'auto', light: 'sun', dark: 'moon' }[themeMode.value]));
        const themeLabel = computed(() => ({ auto: '主题：跟随系统', light: '主题：浅色', dark: '主题：深色' }[themeMode.value]));
        const hasKey = computed(() => !!apiKey.value.trim());

        // 服务状态点：加载时查一次 /v2/health（无鉴权端点），不做持续轮询
        const svc = reactive({ cls: '', title: '服务状态检测中…' });
        onMounted(async () => {
          try {
            const r = await fetch('/v2/health');
            const d = await r.json();
            if (r.ok && d.status === 'ready') {
              svc.cls = 'ok';
              svc.title = '服务就绪 · ' + [d.device, d.model_size, d.asr_backend].filter(Boolean).join(' · ');
            } else {
              svc.cls = 'warn';
              svc.title = '服务未就绪：' + (d.status || ('HTTP ' + r.status));
            }
          } catch (e) {
            svc.cls = 'off';
            svc.title = '服务不可达';
          }
        });

        return { theme, themeOverrides, themeMode, themeIcon, themeLabel, cycleTheme, hasKey, svc, apiKey };
      },
    };
  }

  /* 统一挂载入口：注册 naive 与全局图标组件 */
  function mountApp(AppBody) {
    const app = createApp(makeRoot(AppBody));
    app.use(naive);
    app.component('a-icon', AIcon);
    app.mount('#app');
  }

  return { fmtTime, fmtMs, fmtDate, fmtBytes, spkIdx, apiKey, authHeaders, mountApp };
})();
