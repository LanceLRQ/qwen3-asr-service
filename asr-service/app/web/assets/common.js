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

  /* —— i18n：双态 zh/en；初始 = localStorage 显式选择 > 浏览器语言自动检测 —— */
  const locale = ref(localStorage.getItem('asr_lang')
    || ((navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en'));
  watch(locale, v => { document.documentElement.lang = v === 'zh' ? 'zh-CN' : 'en'; }, { immediate: true });
  /* 仅显式切换才落 localStorage——保留"从未选择过"状态供文档页按浏览器语言跳版本 */
  function setLang(v) { locale.value = v; localStorage.setItem('asr_lang', v); }
  function toggleLang() { setLang(locale.value === 'zh' ? 'en' : 'zh'); }
  function langChosen() { return localStorage.getItem('asr_lang') != null; }
  /* 文案工厂：各页字典就近定义；t() 内读 locale.value，模板/computed 随切换自动重渲染。
   * 占位符 {0}/{1} 按位替换；缺词回退中文再回退 key（漏译可见、不报错）。 */
  function makeT(dict) {
    return (key, ...args) => {
      let s = (dict[locale.value] || {})[key];
      if (s == null) s = (dict.zh || {})[key];
      if (s == null) return key;
      return args.length ? s.replace(/\{(\d+)\}/g, (m, i) => (args[i] != null ? String(args[i]) : m)) : s;
    };
  }

  /* 应用栏共享文案（四页 in-DOM 模板使用 makeRoot 暴露的 t） */
  const COMMON_M = {
    zh: {
      'nav.offline': '离线转写', 'nav.stream': '实时转写', 'nav.speakers': '说话人', 'nav.docs': '文档',
      'key.hint': 'API Key（留空表示无需认证）',
      'theme.auto': '主题：跟随系统', 'theme.light': '主题：浅色', 'theme.dark': '主题：深色',
      'lang.title': '切换语言 / Switch language',
      'svc.checking': '服务状态检测中…', 'svc.ready': '服务就绪', 'svc.notReady': '服务未就绪：', 'svc.unreachable': '服务不可达',
      'model.title': '模型信息', 'model.mode': '运行模式', 'model.device': '设备', 'model.modelSize': '模型规格',
      'model.asrBackend': 'ASR 后端', 'model.vadBackend': 'VAD 后端', 'model.puncBackend': '标点后端',
      'model.align': '词级对齐', 'model.punc': '标点恢复', 'model.speaker': '说话人分离', 'model.speakerDb': '声纹库',
      'model.configFile': '配置文件', 'model.on': '开', 'model.off': '关', 'model.none': '未加载',
      'model.unavailable': '服务信息暂不可用',
    },
    en: {
      'nav.offline': 'Transcribe', 'nav.stream': 'Live', 'nav.speakers': 'Speakers', 'nav.docs': 'Docs',
      'key.hint': 'API Key (leave empty if auth is disabled)',
      'theme.auto': 'Theme: system', 'theme.light': 'Theme: light', 'theme.dark': 'Theme: dark',
      'lang.title': '切换语言 / Switch language',
      'svc.checking': 'Checking service…', 'svc.ready': 'Service ready', 'svc.notReady': 'Service not ready: ', 'svc.unreachable': 'Service unreachable',
      'model.title': 'Model info', 'model.mode': 'Mode', 'model.device': 'Device', 'model.modelSize': 'Model size',
      'model.asrBackend': 'ASR backend', 'model.vadBackend': 'VAD backend', 'model.puncBackend': 'Punc backend',
      'model.align': 'Word align', 'model.punc': 'Punctuation', 'model.speaker': 'Diarization', 'model.speakerDb': 'Voiceprint DB',
      'model.configFile': 'Config file', 'model.on': 'on', 'model.off': 'off', 'model.none': 'none',
      'model.unavailable': 'Service info unavailable',
    },
  };
  const ct = makeT(COMMON_M);

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
    chip: 'M5 5h14v14H5z|M9 9h6v6H9z|M9 2v3|M15 2v3|M9 19v3|M15 19v3|M2 9h3|M2 15h3|M19 9h3|M19 15h3',
    info: 'M12 22a10 10 0 1 1 0-20 10 10 0 0 1 0 20z|M12 8v5|M12 16h.01',
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
        const themeLabel = computed(() => ct({ auto: 'theme.auto', light: 'theme.light', dark: 'theme.dark' }[themeMode.value]));
        const hasKey = computed(() => !!apiKey.value.trim());

        // Naive UI 组件内置文案（空表/分页/确认按钮等）跟随语言
        const naiveLocale = computed(() => (locale.value === 'zh' ? naive.zhCN : naive.enUS));
        const naiveDateLocale = computed(() => (locale.value === 'zh' ? naive.dateZhCN : naive.dateEnUS));
        // 文档导航入口随当前语言直达对应 README（英文 readme / 中文 readme_zh）
        const docsHref = computed(() => (locale.value === 'en' ? '/web-ui/docs/readme' : '/web-ui/docs/readme_zh'));

        // 服务状态点：加载时查一次 /v2/health（无鉴权端点），不做持续轮询；
        // 状态存 key+detail，title 经 computed 翻译（语言切换即时生效）
        const svc = reactive({ cls: '', key: 'checking', detail: '' });
        const svcTitle = computed(() => {
          if (svc.key === 'ready') return ct('svc.ready') + (svc.detail ? ' · ' + svc.detail : '');
          if (svc.key === 'notReady') return ct('svc.notReady') + svc.detail;
          return ct('svc.' + svc.key);
        });
        // 状态短文案（不含设备/模型明细——明细已在下方模型行）：合入模型卡顶部状态行
        const svcState = computed(() => (svc.key === 'ready' ? ct('svc.ready') : svcTitle.value));

        // 模型信息卡片：/v2/health 全量字段（应用栏 chip 图标悬停展示，随语言重渲染）
        const health = reactive({ loaded: false });
        const modelRows = computed(() => {
          if (!health.loaded) return [];
          const rows = [];
          const add = (label, value, cls) => rows.push({ label, value, cls: cls || '' });
          const onoff = (b) => b ? ct('model.on') : ct('model.off');
          add(ct('model.mode'), health.mode);
          add(ct('model.device'), health.device);
          if (health.model_size) add(ct('model.modelSize'), health.model_size);
          if (health.asr_backend) add(ct('model.asrBackend'), health.asr_backend);
          if (health.vad_backend) add(ct('model.vadBackend'), health.vad_backend);
          if (health.punc_backend) add(ct('model.puncBackend'), health.punc_backend);
          add(ct('model.align'), onoff(health.align_enabled), health.align_enabled ? 'on' : 'off');
          add(ct('model.punc'), onoff(health.punc_enabled), health.punc_enabled ? 'on' : 'off');
          add(ct('model.speaker'), onoff(health.speaker_enabled), health.speaker_enabled ? 'on' : 'off');
          add(ct('model.speakerDb'), onoff(health.speaker_db_enabled), health.speaker_db_enabled ? 'on' : 'off');
          add(ct('model.configFile'), health.config_file || ct('model.none'), health.config_file ? '' : 'off');
          return rows;
        });

        onMounted(async () => {
          try {
            const r = await fetch('/v2/health');
            const d = await r.json();
            if (r.ok) { Object.assign(health, d); health.loaded = true; }
            if (r.ok && d.status === 'ready') {
              svc.cls = 'ok';
              svc.key = 'ready';
              svc.detail = [d.device, d.model_size, d.asr_backend].filter(Boolean).join(' · ');
            } else {
              svc.cls = 'warn';
              svc.key = 'notReady';
              svc.detail = d.status || ('HTTP ' + r.status);
            }
          } catch (e) {
            svc.cls = 'off';
            svc.key = 'unreachable';
          }
        });

        return { theme, themeOverrides, themeMode, themeIcon, themeLabel, cycleTheme, hasKey, svc, svcTitle, svcState, apiKey,
                 modelRows, t: ct, locale, toggleLang, docsHref, naiveLocale, naiveDateLocale };
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

  return { fmtTime, fmtMs, fmtDate, fmtBytes, spkIdx, apiKey, authHeaders, mountApp,
           locale, setLang, toggleLang, langChosen, makeT };
})();
