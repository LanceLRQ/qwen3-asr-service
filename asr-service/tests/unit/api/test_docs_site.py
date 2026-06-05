"""文档中心测试（app/web/docs_site.py + /web-ui/docs 路由）。

- 假仓库（tmp_path）验证：注册表白名单、链接重写、锚点 slugify、404、容错。
- 真实仓库验证：docs/plan 绝不进入注册表。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web import docs_site
from app.web.views import web_router


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """构造最小仓库布局并指向它（自动清理渲染缓存）。"""
    (tmp_path / "docs" / "api").mkdir(parents=True)
    (tmp_path / "docs" / "plan").mkdir()
    (tmp_path / "README.md").write_text(
        "# 项目主页\n\n[部署指南](docs/deployment.md)\n", encoding="utf-8"
    )
    (tmp_path / "README_EN.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "docs" / "deployment.md").write_text(
        "# 部署指南\n\n## 启动参数（完整表）\n\n"
        "[API v2](api/v2.md#响应格式) | [回主页](../README.md)\n"
        "[示例配置](../asr-service/config.example.yaml)\n"
        "[官网](https://example.com/x.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "api" / "v2.md").write_text(
        "# API v2\n\n## 响应格式\n\n[配置](../configuration.md)\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "plan" / "secret.md").write_text("# 内部资料\n", encoding="utf-8")
    monkeypatch.setattr(docs_site, "REPO_ROOT", str(tmp_path))
    docs_site.reset_cache()
    yield tmp_path
    docs_site.reset_cache()


def _client():
    app = FastAPI()
    app.include_router(web_router)
    return TestClient(app)


# ---------- 注册表（白名单） ----------

def test_registry_scans_whitelist_only(fake_repo):
    reg = docs_site.get_registry()
    assert set(reg) == {"readme", "readme_en", "deployment", "api/v2"}


def test_registry_excludes_plan(fake_repo):
    assert not any("plan" in s for s in docs_site.get_registry())


def test_real_repo_registry_never_exposes_plan():
    # 真实仓库根：docs/plan/** 大量内部资料必须不可达
    docs_site.reset_cache()
    reg = docs_site.get_registry()
    assert reg, "真实仓库应能扫描到文档"
    assert not any("plan" in info["relpath"] for info in reg.values())
    docs_site.reset_cache()


# ---------- 渲染与链接重写 ----------

def test_render_rewrites_md_links_to_routes(fake_repo):
    page = docs_site.render_doc_page("deployment")
    assert 'href="/web-ui/docs/api/v2#响应格式"' in page      # 带锚点的 .md 链接
    assert 'href="/web-ui/docs/readme"' in page               # ../README.md 归一化
    assert (
        'href="https://github.com/LanceLRQ/qwen3-asr-service/blob/main/'
        'asr-service/config.example.yaml"' in page
    )                                                          # 仓库内非文档文件 → GitHub
    assert 'href="https://example.com/x.md"' in page          # 外链原样保留


def test_render_github_style_anchor_for_chinese_heading(fake_repo):
    page = docs_site.render_doc_page("deployment")
    # GitHub 规则：小写、去标点（含中文括号）、空格转连字符
    assert 'id="启动参数完整表"' in page


def test_render_subdir_doc_resolves_parent_links(fake_repo):
    page = docs_site.render_doc_page("api/v2")
    # docs/api/v2.md 里的 ../configuration.md 不在注册表 → GitHub 兜底
    assert "blob/main/docs/configuration.md" in page


def test_render_caches_page(fake_repo):
    p1 = docs_site.render_doc_page("readme")
    p2 = docs_site.render_doc_page("readme")
    assert p1 is p2


# ---------- 路由 ----------

def test_docs_index_renders_readme(fake_repo):
    resp = _client().get("/web-ui/docs")
    assert resp.status_code == 200
    assert "项目主页" in resp.text
    assert 'href="/web-ui/docs/deployment"' in resp.text


def test_docs_page_ok_and_nav_marks_active(fake_repo):
    resp = _client().get("/web-ui/docs/deployment")
    assert resp.status_code == 200
    assert 'class="active"' in resp.text


def test_docs_unknown_slug_404(fake_repo):
    assert _client().get("/web-ui/docs/nonexistent").status_code == 404


def test_docs_plan_slug_404(fake_repo):
    assert _client().get("/web-ui/docs/plan/secret").status_code == 404


def test_docs_traversal_404(fake_repo):
    c = _client()
    assert c.get("/web-ui/docs/../README").status_code in (404, 400)
    assert c.get("/web-ui/docs/..%2F..%2Fetc%2Fpasswd").status_code in (404, 400)


def test_docs_missing_root_404(tmp_path, monkeypatch):
    # 文档根不存在（异常部署）→ 404 降级，不抛异常
    monkeypatch.setattr(docs_site, "REPO_ROOT", str(tmp_path / "nowhere"))
    docs_site.reset_cache()
    assert _client().get("/web-ui/docs").status_code == 404
    docs_site.reset_cache()


# ---------- 导航语言分组 ----------

def _doc_nav(page: str) -> str:
    """提取文档侧边导航（页面首个 nav 是应用栏，需定位 docs-sidebar）。"""
    return page.split('class="docs-sidebar"')[1].split("</nav>")[0]


def test_nav_groups_by_language(fake_repo):
    zh = docs_site.render_doc_page("readme")
    assert "/web-ui/docs/readme_en" not in _doc_nav(zh)   # 中文导航不混英文文档
    en = docs_site.render_doc_page("readme_en")
    assert "/web-ui/docs/readme_en" in _doc_nav(en)


# ---------- Studio Console 外壳 ----------

def test_docs_page_uses_console_shell(fake_repo):
    """文档页接入新版 UI 框架：vendor 脚本 + 共享样式 + v-pre 保护服务端 HTML。"""
    page = docs_site.render_doc_page("readme")
    assert "/web-ui/assets/vendor/vue-3.5.35.global.prod.js" in page
    assert "/web-ui/assets/vendor/naive-ui-2.44.1.prod.js" in page
    assert "/web-ui/assets/app.css" in page
    assert "/web-ui/assets/common.js" in page
    assert 'class="markdown-body" v-pre' in page   # 正文跳过 Vue 编译（防 {{ }} 示例被误解析）
    assert 'class="appbar"' in page
