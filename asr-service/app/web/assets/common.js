/* Web UI 公共模块（无构建 UMD）：主题根组件工厂 + 时间格式化。
 * 依赖全局 Vue / naive（vendor 脚本先加载）。
 */
window.AsrCommon = (function () {
  'use strict';
  const { ref, computed, watch } = Vue;

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

  /* 构造页面根组件：n-config-provider 主题（跟随系统 + 手动循环切换，localStorage 记忆），
   * in-DOM 根模板通过 :theme-mode / @cycle-theme 与 app-body 通信。 */
  function makeRoot(AppBody) {
    return {
      components: { 'app-body': AppBody },
      setup() {
        const osTheme = naive.useOsTheme();
        const themeMode = ref(localStorage.getItem('asr_theme') || 'auto'); // auto | light | dark
        const isDark = computed(() => (themeMode.value === 'auto' ? osTheme.value === 'dark' : themeMode.value === 'dark'));
        const theme = computed(() => (isDark.value ? naive.darkTheme : null));
        watch(isDark, v => document.body.classList.toggle('dark', v), { immediate: true });
        const themeOverrides = {
          common: { primaryColor: '#6366f1', primaryColorHover: '#4f46e5', primaryColorPressed: '#4338ca', primaryColorSuppl: '#6366f1' },
        };
        function cycleTheme() {
          const order = ['auto', 'light', 'dark'];
          themeMode.value = order[(order.indexOf(themeMode.value) + 1) % order.length];
          localStorage.setItem('asr_theme', themeMode.value);
        }
        return { theme, themeOverrides, themeMode, cycleTheme };
      },
    };
  }

  return { fmtTime, fmtMs, fmtDate, makeRoot };
})();
