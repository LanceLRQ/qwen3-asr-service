"""文档中心：服务端渲染仓库 Markdown 文档（GET /web-ui/docs）。

- 文档注册表为白名单：仅扫描仓库根 README*.md + docs/*.md + docs/api/*.md
  两层固定目录，不递归（docs/plan/** 为内部资料，绝不暴露）；请求 slug 只在
  注册表内查找，不做任何文件系统路径拼接，天然防穿越。
- 标题锚点使用 GitHub 风格 slugify（中文标题可用），与文档内既有 #锚点 链接对齐。
- 文档间 .md 相对链接重写为 /web-ui/docs/<slug> 路由；指向仓库内其它文件的
  相对链接（如 config.example.yaml）重写为 GitHub blob 绝对链接。
"""

import html
import logging
import os
import posixpath
import re

import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

GITHUB_BLOB_BASE = "https://github.com/LanceLRQ/qwen3-asr-service/blob/main"
DOCS_ROUTE_PREFIX = "/web-ui/docs"

# 仓库根：源码部署 = asr-service 的父目录；Docker 镜像 COPY docs/ → /docs、
# README*.md → / 保持同布局（WORKDIR /app 的父目录即 /）
REPO_ROOT = os.path.dirname(BASE_DIR)

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "docs_template.html")
_template: str | None = None


def _get_template() -> str | None:
    """懒加载页面模板；缺失时记录错误并返回 None（路由层转 404），不影响模块导入。"""
    global _template
    if _template is None:
        try:
            with open(_TEMPLATE_PATH, encoding="utf-8") as f:
                _template = f.read()
        except OSError as e:
            logger.error(f"文档模板读取失败: {e}")
            return None
    return _template

# 侧边导航顺序与短标题（slug 去 _en 后缀为键；不在表中的文档排在末尾、用文档 h1 兜底）
_NAV_ORDER = ["readme", "deployment", "configuration", "api/v2", "api/v1", "architecture"]
_NAV_TITLES = {
    "readme": ("项目主页", "Home"),
    "deployment": ("部署指南", "Deployment"),
    "configuration": ("配置文档", "Configuration"),
    "api/v2": ("API v2（默认）", "API v2 (default)"),
    "api/v1": ("API v1（兼容）", "API v1 (legacy)"),
    "architecture": ("架构说明", "Architecture"),
}

_registry: dict | None = None
_page_cache: dict[str, str] = {}


def _github_slugify(value: str, separator: str) -> str:
    """GitHub 风格标题锚点：小写、去标点（保留 unicode 字母数字/下划线/连字符）、空格转连字符。"""
    value = value.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return value.replace(" ", separator)


def _slug_for(relpath: str) -> str:
    """README.md → readme；docs/x.md → x；docs/api/x.md → api/x（统一小写）。"""
    if relpath.startswith("README"):
        return relpath[: -len(".md")].lower()
    return relpath[len("docs/"): -len(".md")].lower()


def _read_title(path: str) -> str | None:
    """取文档首个一级标题作为页面标题。"""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return None


def _scan_registry() -> dict:
    """扫描白名单目录构建 slug → {relpath, title} 注册表。"""
    candidates = ["README.md", "README_EN.md"]
    for sub in ("docs", "docs/api"):
        d = os.path.join(REPO_ROOT, sub)
        if os.path.isdir(d):
            candidates += [
                f"{sub}/{name}" for name in sorted(os.listdir(d)) if name.endswith(".md")
            ]
    registry = {}
    repo_real = os.path.realpath(REPO_ROOT)
    for rel in candidates:
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.isfile(path):
            continue
        # 防符号链接逃逸：实际路径必须落在仓库根内
        if os.path.commonpath([os.path.realpath(path), repo_real]) != repo_real:
            logger.warning(f"文档 {rel} 实际路径越出仓库根，已跳过")
            continue
        slug = _slug_for(rel)
        registry[slug] = {
            "relpath": rel,
            "title": _read_title(path) or os.path.basename(rel),
        }
    return registry


def get_registry() -> dict:
    global _registry
    if _registry is None:
        _registry = _scan_registry()
        if not _registry:
            logger.warning(
                f"文档中心未发现任何文档（扫描根：{REPO_ROOT}），/web-ui/docs 将返回 404"
            )
    return _registry


def reset_cache() -> None:
    """清空注册表与渲染缓存（测试用）。"""
    global _registry
    _registry = None
    _page_cache.clear()


class _LinkRewriter(Treeprocessor):
    """重写 <a href>：.md 相对链接 → 文档路由；其它仓库内相对链接 → GitHub blob。"""

    def __init__(self, md, cur_dir: str, rel_to_slug: dict):
        super().__init__(md)
        self._cur_dir = cur_dir  # 当前文档相对仓库根的目录（"" / "docs" / "docs/api"）
        self._rel_to_slug = rel_to_slug

    def run(self, root):
        for a in root.iter("a"):
            href = a.get("href")
            if href:
                a.set("href", self._rewrite(href))

    def _rewrite(self, href: str) -> str:
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return href
        path, _, anchor = href.partition("#")
        suffix = f"#{anchor}" if anchor else ""
        path = path.partition("?")[0]  # 剥离查询字符串，避免 slug 匹配失败
        norm = posixpath.normpath(posixpath.join(self._cur_dir, path))
        slug = self._rel_to_slug.get(norm)
        if slug is not None:
            return f"{DOCS_ROUTE_PREFIX}/{slug}{suffix}"
        if norm.startswith(".."):  # 越出仓库根，无法定位，原样保留
            return href
        return f"{GITHUB_BLOB_BASE}/{norm}{suffix}"


class _LinkRewriteExtension(Extension):
    def __init__(self, cur_dir: str, rel_to_slug: dict):
        super().__init__()
        self._cur_dir = cur_dir
        self._rel_to_slug = rel_to_slug

    def extendMarkdown(self, md):
        md.treeprocessors.register(
            _LinkRewriter(md, self._cur_dir, self._rel_to_slug), "docs_link_rewrite", 5
        )


def _build_nav(active_slug: str, registry: dict) -> str:
    """生成与当前文档同语言的侧边导航（文档头部自带中英切换链接）。"""
    is_en = active_slug.endswith("_en")

    def sort_key(slug: str):
        base = slug[: -len("_en")] if slug.endswith("_en") else slug
        try:
            return (_NAV_ORDER.index(base), slug)
        except ValueError:
            return (len(_NAV_ORDER), slug)

    items = []
    for slug in sorted(registry, key=sort_key):
        if slug.endswith("_en") != is_en:
            continue
        base = slug[: -len("_en")] if is_en else slug
        titles = _NAV_TITLES.get(base)
        label = titles[1 if is_en else 0] if titles else registry[slug]["title"]
        cls = ' class="active"' if slug == active_slug else ""
        items.append(
            f'<a href="{DOCS_ROUTE_PREFIX}/{slug}"{cls}>{html.escape(label)}</a>'
        )
    return "\n      ".join(items)


def render_doc_page(slug: str) -> str | None:
    """渲染整页 HTML；slug 不在注册表或文档不可读时返回 None（路由层转 404）。"""
    registry = get_registry()
    info = registry.get(slug)
    if info is None:
        return None
    cached = _page_cache.get(slug)
    if cached is not None:
        return cached

    path = os.path.join(REPO_ROOT, info["relpath"])
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        logger.warning(f"文档读取失败 {info['relpath']}: {e}")
        return None

    template = _get_template()
    if template is None:
        return None

    rel_to_slug = {v["relpath"]: k for k, v in registry.items()}
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "toc",
            _LinkRewriteExtension(posixpath.dirname(info["relpath"]), rel_to_slug),
        ],
        extension_configs={"toc": {"slugify": _github_slugify}},
    )
    page = (
        template
        .replace("__DOC_TITLE__", html.escape(info["title"]))
        .replace("__DOC_NAV__", _build_nav(slug, registry))
        .replace("__DOC_BODY__", md.convert(text))
    )
    _page_cache[slug] = page
    return page
