"""公司：基本信息、启用的角色及其模型配置、共享文件区。保存在 ~/.opc/companies/<id>/。

注意：公司配置是静态的"运行时上下文"，不放进 LangGraph 的 state；state 只保存会变化的东西（任务、对话、用量）。
"""
import difflib
import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import effort, model_settings, providers, prompts
from .config import LLMConfig, opc_home
from .llm import LLM
from .roles import ROLES

MAX_FILE_BYTES = 500_000


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _doc_title(p: Path) -> str:
    """文档标题：开头部分的第一个 Markdown 标题（成果卡片上显示）。"""
    try:
        with p.open(encoding="utf-8", errors="replace") as f:
            head = f.read(4000)
    except OSError:
        return ""
    for line in head.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:80]
    return ""


class FileError(Exception):
    """文件操作错误，消息会作为工具结果返回给模型。"""


def _atomic_write(p: Path, content: str) -> None:
    """先写临时文件再整体替换：别人读文件时不会读到写了一半的内容。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:  # Windows：目标文件正被别的程序（杀毒软件、编辑器）短暂打开，稍等再试
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if tmp.exists():
            tmp.unlink()


def _is_temp(p: Path) -> bool:
    return p.name.startswith(".") and p.name.endswith(".tmp")


CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _is_cache(rel: Path) -> bool:
    """运行代码和测试产生的缓存（字节码等）：不是谁的产出，不显示在成果里（v1.0 验收时 .pyc 出现在了成果列表里）。"""
    return rel.suffix == ".pyc" or any(part in CACHE_DIRS for part in rel.parts)


class Company:
    def __init__(self, meta: dict, root: Path, llm_factory=None):
        self.meta = meta
        self.root = root
        self.files_root = root / "files"
        self.files_root.mkdir(parents=True, exist_ok=True)
        self._llm_factory = llm_factory or (lambda cfg, role_id: LLM(cfg))
        self._lock = threading.Lock()  # 多个角色并行写文件时保护元数据
        self._file_locks: dict[str, threading.Lock] = {}  # 每个文件一把锁：读出、修改、写回之间不会被别人插进来

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def role_ids(self) -> list[str]:
        return [r["id"] for r in self.meta["roles"]]

    @property
    def home(self) -> Path:
        """数据目录（companies/<id> 的上两级），模型连接的设置在这里。"""
        return self.root.parent.parent

    def settings(self) -> dict:
        return model_settings.load(self.home)

    def llm_config(self, role_id: str | None, settings: dict | None = None) -> LLMConfig:
        """三层：角色单独选的模型 → 公司默认模型 → 全局默认模型。role_id 为 None 表示规划和汇报用的模型（公司默认）。

        v0.14 以前的公司在角色上直接存了服务商、模型、地址、密钥：仍然有效，没填的项用公司默认值；
        选了和默认不同的服务商时，接口地址和模型用那个服务商的默认值，不能沿用默认配置的（地址不对）。
        """
        settings = settings or self.settings()
        base = model_settings.config(self.meta.get("model"), settings) or model_settings.default_config(settings)
        override = next((r for r in self.meta["roles"] if r["id"] == role_id), {}) if role_id else {}
        chosen = model_settings.config(override.get("model"), settings) if isinstance(override.get("model"), dict) else None
        if chosen:
            return chosen
        override = {k: v for k, v in override.items() if isinstance(v, str)}
        if not any(override.get(k) for k in ("provider", "model", "base_url", "api_key")):
            return base
        provider = override.get("provider") or (
            providers.detect(override["base_url"]) if override.get("base_url") else base.provider)
        p = providers.get(provider)
        same = provider == base.provider and not override.get("base_url")
        return LLMConfig(api_key=override.get("api_key") or base.api_key,
                         base_url=override.get("base_url") or (base.base_url if same else p.base_url),
                         model=override.get("model") or (base.model if same else (p.models[0] if p.models else base.model)),
                         provider=provider)

    def llm_for(self, role_id: str | None):
        return self._llm_factory(self.llm_config(role_id), role_id)

    def public_meta(self) -> dict:
        """发给浏览器的公司信息：API Key 只显示是否已设置。"""
        settings = self.settings()
        roles = []
        for r in self.meta["roles"]:
            cfg = self.llm_config(r["id"], settings)
            ref = r.get("model") if isinstance(r.get("model"), dict) else None
            legacy = not ref and any(isinstance(r.get(k), str) and r[k] for k in ("provider", "model", "base_url", "api_key"))
            roles.append({"id": r["id"], "model": cfg.model, "base_url": cfg.base_url, "provider": cfg.provider,
                          "model_ref": ref, "legacy_model": legacy, "prompt_level": self.prompt_level(r["id"])})
        meta = {k: v for k, v in self.meta.items() if k not in ("roles", "model")}
        label = model_settings.describe(self.meta.get("model"), settings)
        for r, entry in zip(roles, self.meta["roles"]):  # 价格（元/百万 token，可选）：界面按它估算花费
            r["price"] = model_settings.price(self.model_ref(entry["id"], settings), settings)
        return {**meta, "roles": roles, "model_ref": self.meta.get("model"), "effort": self.effort, "kb": self.kb,
                "planner_price": model_settings.price(self.model_ref(None, settings), settings),
                "model_label": label or f"全局默认（{model_settings.describe(settings['default'], settings)}）"}

    def model_ref(self, role_id: str | None, settings: dict) -> dict | None:
        """这个角色实际用的模型连接引用（角色单独选的 → 公司默认 → 全局默认）；旧版本直接写在角色上的配置返回 None。"""
        entry = next((r for r in self.meta["roles"] if r["id"] == role_id), {}) if role_id else {}
        if isinstance(entry.get("model"), dict) and model_settings.config(entry["model"], settings):
            return entry["model"]
        if any(isinstance(entry.get(k), str) and entry[k] for k in ("provider", "model", "base_url", "api_key")):
            return None
        if model_settings.config(self.meta.get("model"), settings):
            return self.meta["model"]
        return settings["default"]

    def set_models(self, default, role_models: dict) -> None:
        """改公司默认模型和各角色的选择（role_models：角色 id -> 引用，空表示跟随公司默认）。
        改过的角色上旧版本留下的服务商、密钥等字段一并清掉，以后只按引用找。"""
        settings = self.settings()
        default = model_settings.clean_ref(default, settings)
        refs = {rid: model_settings.clean_ref(ref, settings) for rid, ref in (role_models or {}).items()}
        with self._lock:
            if default:
                self.meta["model"] = default
            else:
                self.meta.pop("model", None)
            for r in self.meta["roles"]:
                if r["id"] not in refs:
                    continue
                for k in ("provider", "model", "base_url", "api_key"):
                    r.pop(k, None)
                if refs[r["id"]]:
                    r["model"] = refs[r["id"]]
            _atomic_write(self.root / "company.json", json.dumps(self.meta, ensure_ascii=False, indent=1))

    # ---------- 共享文件区 ----------

    def resolve(self, path: str) -> Path:
        p = (self.files_root / path.strip().lstrip("/\\")).resolve()
        if not p.is_relative_to(self.files_root.resolve()):
            raise FileError(f"路径 {path} 不在公司文件区内")
        return p

    def _meta_path(self) -> Path:
        return self.root / "files_meta.json"

    def file_meta(self) -> dict:
        try:
            return json.loads(self._meta_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def list_files(self) -> list[dict]:
        meta = self.file_meta()
        out = []
        for p in sorted(self.files_root.rglob("*")):
            if p.is_file() and not _is_temp(p) and not _is_cache(p.relative_to(self.files_root)):
                rel = p.relative_to(self.files_root).as_posix()
                item = {"path": rel, "size": p.stat().st_size, **meta.get(rel, {})}
                if p.suffix.lower() in (".md", ".markdown"):
                    item["title"] = _doc_title(p)
                out.append(item)
        return out

    def read_file(self, path: str) -> str:
        p = self.resolve(path)
        with self._file_lock(p):  # 不和正在进行的写入交错（Windows 上读着的文件不能被替换）
            if not p.is_file():
                raise FileError(f"文件不存在：{path}")
            if p.stat().st_size > MAX_FILE_BYTES:
                raise FileError(f"文件太大：{path}")
            return p.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str, author: str) -> bool:
        """写入文件并记录作者。返回是否是新建的文件。"""
        p = self._writable(path)
        with self._file_lock(p):
            created = not p.exists()
            _atomic_write(p, content)
        self.record_author(path, author)
        return created

    def remove_file(self, path: str) -> bool:
        """删除文件区里的一个文件（系统用，例如过时的简洁版）。返回是否删了。"""
        p = self._writable(path)
        with self._file_lock(p):
            if not p.is_file():
                return False
            p.unlink()
        return True

    def modify_file(self, path: str, change, author: str):
        """读出、修改、写回一个已有文件，整个过程持有这个文件的锁（并行的同事不会互相覆盖）。

        change(旧内容) -> (新内容, 返回值)；change 抛出的异常原样传出，文件不变。
        """
        p = self._writable(path)
        with self._file_lock(p):
            if not p.is_file():
                raise FileError(f"文件不存在：{path}")
            new_text, result = change(p.read_text(encoding="utf-8", errors="replace"))
            _atomic_write(p, new_text)
        self.record_author(path, author)
        return result

    def _writable(self, path: str) -> Path:
        p = self.resolve(path)
        if p == self.files_root.resolve():
            raise FileError("需要提供文件名")
        return p

    def _file_lock(self, p: Path) -> threading.Lock:
        with self._lock:
            return self._file_locks.setdefault(str(p), threading.Lock())

    # ---------- 快照：返工前保存产出文件，下游恢复时生成改动对比 ----------

    def snapshot(self, tag: str, paths: list[str]) -> str:
        """把这些文件复制到 snapshots/<tag>/（不在共享文件区里，角色看不到）。返回标签。"""
        base = self.root / "snapshots" / tag
        for path in paths:
            try:
                src = self.resolve(path)
            except FileError:
                continue
            if src.is_file():
                dst = base / src.relative_to(self.files_root.resolve())
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
        return tag

    def diff_since(self, tag: str, paths: list[str], limit: int = 12_000) -> str:
        """快照之后这些文件改了什么（unified diff），太长时截断。没有变化返回空字符串。"""
        base = self.root / "snapshots" / tag
        parts = []
        for path in paths:
            try:
                cur_path = self.resolve(path)
            except FileError:
                continue
            rel = cur_path.relative_to(self.files_root.resolve()).as_posix()
            old_path = base / rel
            old = old_path.read_text(encoding="utf-8", errors="replace").splitlines() if old_path.is_file() else []
            new = cur_path.read_text(encoding="utf-8", errors="replace").splitlines() if cur_path.is_file() else []
            if old != new:
                parts += list(difflib.unified_diff(old, new, f"{rel}（修改前）", f"{rel}（修改后）", n=2, lineterm=""))
        text = "\n".join(parts)
        return text if len(text) <= limit else text[:limit] + f"\n…（改动对比太长，只显示前 {limit} 字，完整内容请读文件）"

    def record_author(self, path: str, author: str) -> None:
        """记录文件的最后修改者（编码 agent 直接写文件时也通过这里登记）。"""
        rel = self.resolve(path).relative_to(self.files_root.resolve()).as_posix()
        with self._lock:
            meta = self.file_meta()
            meta[rel] = {"author": author, "updated": now()}
            self._meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def prompt_level(self, role_id: str) -> str:
        """这个角色用哪个版本的提示词（简洁 / 完整）：角色单独设置的优先，否则用公司的设置。"""
        entry = next((r for r in self.meta["roles"] if r["id"] == role_id), {})
        return prompts.normalize(entry.get("prompt_level") or self.meta.get("prompt_level"))

    @property
    def effort(self) -> str:
        """投入档位（节省 / 标准 / 充分），见 effort.py。"""
        return effort.normalize(self.meta.get("effort"))

    def set_effort(self, level: str) -> None:
        if level not in effort.LEVELS:
            raise ValueError(f"未知的投入档位：{level}")
        with self._lock:
            self.meta["effort"] = level
            _atomic_write(self.root / "company.json", json.dumps(self.meta, ensure_ascii=False, indent=1))

    @property
    def web(self) -> bool:
        """是否允许角色联网搜索和读网页（经 MCP 服务器，创建公司时选择）。"""
        return bool(self.meta.get("web", False))

    @property
    def kb(self) -> bool:
        """角色能不能检索公司知识库（长期记忆）：默认打开，以前创建的公司没有这个设置也当作打开。"""
        return bool(self.meta.get("kb", True))

    def set_kb(self, enabled: bool) -> None:
        with self._lock:
            self.meta["kb"] = bool(enabled)
            _atomic_write(self.root / "company.json", json.dumps(self.meta, ensure_ascii=False, indent=1))

    def mark_sandboxed(self) -> None:
        """这家公司运行代码用过 Docker 沙箱：之后 Docker 用不了时不悄悄改在本机运行（见 coding.sandbox_for）。"""
        if not self.meta.get("sandboxed"):
            with self._lock:
                self.meta["sandboxed"] = True
                _atomic_write(self.root / "company.json", json.dumps(self.meta, ensure_ascii=False, indent=1))

    @property
    def allow_run(self) -> bool:
        """是否允许工程师通过编码 agent 运行命令（创建公司时选择）。"""
        return bool(self.meta.get("allow_run", False))


class CompanyStore:
    def __init__(self, home: Path | None = None, llm_factory=None):
        self.home = Path(home or opc_home())
        self.dir = self.home / "companies"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.home / "checkpoints.sqlite"
        self.llm_factory = llm_factory

    def _root(self, company_id: str) -> Path:
        if not company_id or not all(c.isalnum() or c == "-" for c in company_id):
            raise ValueError("非法的公司 id")
        return self.dir / company_id

    def create(self, name: str, mission: str, roles: list[dict], allow_run: bool = False, web: bool = False,
               prompt_level: str = prompts.DEFAULT_LEVEL, model: dict | None = None,
               effort_level: str = effort.DEFAULT, kb: bool = True) -> Company:
        """model：公司默认模型（模型连接的引用），空表示用全局默认。角色的 model 同样是引用，空表示跟随公司默认；
        旧接口在角色上直接传服务商、地址、密钥仍然支持。"""
        name = name.strip()
        if not name:
            raise ValueError("请填写公司名称")
        settings = model_settings.load(self.home)
        company_model = model_settings.clean_ref(model, settings)
        role_entries, seen = [], set()
        for r in roles:
            if r.get("id") not in ROLES:
                raise ValueError(f"未知角色：{r.get('id')}")
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            entry = {"id": r["id"]}
            ref = model_settings.clean_ref(r.get("model"), settings) if isinstance(r.get("model"), dict) else None
            if ref:
                entry["model"] = ref
            if r.get("provider") and r["provider"] not in providers.PROVIDERS:
                raise ValueError(f"未知的服务商：{r['provider']}")
            if r.get("provider") == "custom" and not str(r.get("base_url") or "").strip():
                raise ValueError("自定义服务商需要填写接口地址")
            for key in ("provider", "model", "base_url", "api_key"):
                if not ref and isinstance(r.get(key), str) and r[key].strip():
                    entry[key] = str(r[key]).strip()
            if r.get("prompt_level"):
                if not prompts.is_valid(r["prompt_level"]):
                    raise ValueError(f"未知的提示词版本：{r['prompt_level']}")
                entry["prompt_level"] = prompts.normalize(r["prompt_level"])
            role_entries.append(entry)
        if not role_entries:
            raise ValueError("至少选择一个角色")
        if effort_level not in effort.LEVELS:
            raise ValueError(f"未知的投入档位：{effort_level}")
        company_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        meta = {"id": company_id, "name": name, "mission": mission.strip(), "roles": role_entries,
                "allow_run": bool(allow_run), "web": bool(web), "kb": bool(kb),
                "prompt_level": prompts.normalize(prompt_level), "created": now()}
        if company_model:
            meta["model"] = company_model
        meta["effort"] = effort_level
        root = self._root(company_id)
        root.mkdir(parents=True)
        (root / "company.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return Company(meta, root, self.llm_factory)

    def load(self, company_id: str) -> Company | None:
        try:
            root = self._root(company_id)
            meta = json.loads((root / "company.json").read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError):
            return None
        return Company(meta, root, self.llm_factory)

    def list(self) -> list[dict]:
        items = []
        for p in self.dir.glob("*/company.json"):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append({"id": m["id"], "name": m["name"], "mission": m.get("mission", ""),
                          "roles": [r["id"] for r in m["roles"]], "created": m.get("created", "")})
        return sorted(items, key=lambda d: d["created"], reverse=True)

    def delete(self, company_id: str) -> None:
        try:
            shutil.rmtree(self._root(company_id), ignore_errors=True)
        except ValueError:
            pass
