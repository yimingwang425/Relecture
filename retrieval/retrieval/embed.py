"""Embedding 提供方与磁盘缓存。

## 缓存是必需项，不是优化

缓存键 = ``(provider, model, dimensions, embed_text_ruleset_version, sha256(text))``。

理由**不是省钱**（全矩阵 < $0.02），是**可复现**：同一份报告重跑必须得到同一组
数字，否则 A/B 结论不可信。缓存键里带口径版本，是为了口径一改就自动取新向量，
而不会拿旧向量冒充新口径——那种错误会表现为「改了文本变体但分数没变」，
很容易被读成「文本变体没用」。

**更要命的一层：对不上了你也不知道。** OpenAI 更新 ``text-embedding-3-small``
背后的模型不会有通知，而缓存键里的 model 字段仍是那个字符串——**键不变，向量变了**。
症状是「同一份报告重跑，数字不同」，而人的第一反应是去代码里找 bug，
不会怀疑一个名字没变的模型。

所以 ``cache/`` **不是可再生的中间产物**：它不入库（见 ``.gitignore``），
是 ``runs/`` 里那些数字的唯一副本，删了不可逆。省下的是 $0.02，
赔掉的是「那份报告还能不能复现」——54M 换掉这个很便宜。
（补记 2026-09-09）

## FakeEmbedder 的用途与限制

``FakeEmbedder`` 让整条链路在没有 API key 时可跑通，用于验证**确定性**：
同一输入重跑必须逐位相同。若这条有 bug，在真实数据上会表现为「A/B 结论不稳定」，
而人的第一反应会是怀疑模型，不是怀疑缓存。

**它是词袋哈希，不是语义模型。** 它只对字面重合有反应，
因此在 `synonym` 组上系统性接近随机——**那是评测集刻意反字面复用设计的结果，
不是任何真实模型的预期表现**。用它产出的数字只能看格式，不能看结论。

决策日期：2026-08-21
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Protocol

import numpy as np

from .compose import EMBED_TEXT_RULESET_VERSION

__all__ = [
    "Embedder",
    "FakeEmbedder",
    "PlantedEmbedder",
    "OpenAIEmbedder",
    "EmbeddingCache",
    "embed_texts",
]

_TOKEN = re.compile(r"[a-z0-9]+|[一-鿿]")


class Embedder(Protocol):
    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


class FakeEmbedder:
    """确定性的词袋哈希向量。**仅供链路验证，不产出可解读的检索结论。**

    每个 token 按 sha256 映射到固定桶位，故：

    - 与调用顺序无关（不使用全局 RNG——全局 RNG 会让向量取决于调用次序，
      那正是「A/B 结论不稳定」的典型成因）
    - 与进程无关（不依赖 ``hash()`` 的随机化种子）
    """

    def __init__(self, dimensions: int = 256, seed: str = "fake-v1") -> None:
        self.name = f"fake-{dimensions}"
        self.dimensions = dimensions
        self.seed = seed

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(self.dimensions, dtype=np.float64)
        toks = _TOKEN.findall(text.lower())
        if not toks:
            return v
        for tok, cnt in _counter(toks).items():
            h = hashlib.sha256(f"{self.seed}:{tok}".encode()).digest()
            idx = int.from_bytes(h[:4], "big") % self.dimensions
            sign = 1.0 if h[4] & 1 else -1.0
            # 次线性词频权重：压制长切片里高频词的支配作用
            v[idx] += sign * (1.0 + np.log(cnt))
        return v

    def embed(self, texts: list[str]) -> np.ndarray:
        return _l2(np.stack([self._one(t) for t in texts]))


def _counter(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it] = out.get(it, 0) + 1
    return out


class PlantedEmbedder:
    """**构造**的向量：把已知答案按固定强度种进去。仅用于格式样张与测试夹具。

    ## 为什么需要它，而不是只用 FakeEmbedder

    ``FakeEmbedder`` 是词袋哈希，而本语料是英文、评测集问题是中文
    （评测集刻意用学生的中文说法提问），两侧 token 零重合 →
    **所有指标恒为 0**。

    恒为 0 的链路验证是无效验证：**检索逻辑整个删掉，它也照样通过**。
    这正是第七条通则（验证动作本身也需要被验证）说的情形——
    测试跑绿了，但它没有在测目标现象。

    ``PlantedEmbedder`` 按 ``qid`` 决定每题的信号强度 α，使部分题命中、
    部分题落空，指标因此产生**非退化的、可比对的**数值。于是：

    - 可复现性检查走的是有区分度的路径，而不是一排 0
    - 单元测试能断言「命中时 full-hit 为真、漏时为假」

    ## 它不是预测

    数字由 α 直接决定，**与任何真实模型的表现无关**。
    输出只可用于判断报告格式，不可解读为检索结论。
    """

    def __init__(
        self,
        gold_by_question: dict[str, list[tuple[str, str]]],
        dimensions: int = 256,
        seed: str = "planted-v1",
    ) -> None:
        self.name = f"planted-{dimensions}"
        self.dimensions = dimensions
        self.seed = seed
        self._gold = gold_by_question
        self._qid_of: dict[str, str] = {}
        self._key_of: dict[str, tuple[str, str]] = {}

    def register_question(self, text: str, qid: str) -> None:
        self._qid_of[text] = qid

    def register_slice(self, text: str, key: tuple[str, str]) -> None:
        self._key_of[text] = key

    def _basis(self, token: str) -> np.ndarray:
        h = hashlib.sha256(f"{self.seed}:{token}".encode()).digest()
        rng = np.random.default_rng(int.from_bytes(h[:8], "big"))
        return _l2(rng.standard_normal(self.dimensions))

    def _alpha(self, qid: str) -> float:
        """按 qid 定的信号强度，落在 [0.15, 0.95]，确定且分散。"""
        h = hashlib.sha256(f"{self.seed}:alpha:{qid}".encode()).digest()
        return 0.15 + (int.from_bytes(h[:4], "big") % 1000) / 1000 * 0.80

    def _one(self, text: str) -> np.ndarray:
        key = self._key_of.get(text)
        if key is not None:
            return self._basis(f"slice:{key[0]}:{key[1]}")
        qid = self._qid_of.get(text)
        if qid is None:
            return self._basis(f"unknown:{text}")
        gold = self._gold.get(qid) or []
        noise = self._basis(f"q:{qid}")
        if not gold:
            return noise  # no_answer：纯噪声，没有可种的答案
        target = _l2(np.sum([self._basis(f"slice:{f}:{s}") for f, s in gold], axis=0))
        a = self._alpha(qid)
        return _l2(a * target + (1 - a) * noise)

    def embed(self, texts: list[str]) -> np.ndarray:
        return _l2(np.stack([self._one(t) for t in texts]))


class OpenAIEmbedder:
    """OpenAI ``text-embedding-3-*``。

    ``dimensions`` 走该系列支持的截断（Matryoshka）；``None`` 表示用模型默认维度。

    ⚠️ pgvector 的 ``hnsw`` / ``ivfflat`` 索引上限 **2000 维**，
    ``-large`` 的默认 3072 维无法建近似索引，只能精确扫描。
    对 v1（单门课数十切片）无碍，但选型时要知道这一条。
    """

    def __init__(self, model: str, dimensions: int | None = None, batch: int = 128) -> None:
        self.model = model
        self.name = f"{model}@{dimensions or 'default'}"
        self._dimensions = dimensions
        self.batch = batch
        self._client = None
        self.total_tokens = 0

    @property
    def dimensions(self) -> int:
        if self._dimensions:
            return self._dimensions
        return {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}[self.model]

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise RuntimeError(
                    "未设置 OPENAI_API_KEY。按 实验脚本/漂移实验/README.md 的约定："
                    "放在本目录 .env（已 gitignore），局部注入不要全局 export，例如\n"
                    "  env $(grep -v '^#' .env | xargs) .venv/bin/python -m scripts.run_matrix"
                )
            self._client = OpenAI(api_key=key)
        return self._client

    def embed(self, texts: list[str]) -> np.ndarray:
        client = self._get_client()
        vecs: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i : i + self.batch]
            kwargs = {"model": self.model, "input": chunk}
            if self._dimensions:
                kwargs["dimensions"] = self._dimensions
            resp = client.embeddings.create(**kwargs)
            # token 数以 API 返回的 usage 为准，不自己估——故不引入 tiktoken
            self.total_tokens += resp.usage.total_tokens
            vecs.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        return _l2(np.asarray(vecs, dtype=np.float64))


class EmbeddingCache:
    """按文本内容哈希缓存向量。

    **注意**：这里用内容哈希做键是**本地进程内的可复现性手段**，
    与 PRD §12.1 禁止的「按内容哈希做跨用户去重键」不是一回事——
    本缓存只存评测语料的向量，不涉及用户数据、不跨用户共享。
    生产侧的向量存储走 Supabase 并由 RLS 按用户隔离，见 ``sql/001_slices.sql``。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _path(self, embedder: Embedder, text: str) -> Path:
        key = hashlib.sha256(
            "\x00".join(
                [
                    getattr(embedder, "name", "?"),
                    str(embedder.dimensions),
                    EMBED_TEXT_RULESET_VERSION,
                    text,
                ]
            ).encode()
        ).hexdigest()
        return self.root / key[:2] / f"{key}.json"

    def get_or_compute(self, embedder: Embedder, texts: list[str]) -> np.ndarray:
        paths = [self._path(embedder, t) for t in texts]
        out: list[np.ndarray | None] = []
        todo: list[int] = []
        for i, p in enumerate(paths):
            if p.exists():
                out.append(np.asarray(json.loads(p.read_text())["v"], dtype=np.float64))
                self.hits += 1
            else:
                out.append(None)
                todo.append(i)
                self.misses += 1
        if todo:
            fresh = embedder.embed([texts[i] for i in todo])
            for slot, i in enumerate(todo):
                vec = fresh[slot]
                out[i] = vec
                paths[i].parent.mkdir(parents=True, exist_ok=True)
                paths[i].write_text(json.dumps({"v": vec.tolist()}))
        return np.stack([v for v in out if v is not None])


def embed_texts(embedder: Embedder, texts: list[str], cache: EmbeddingCache | None) -> np.ndarray:
    if cache is None:
        return embedder.embed(texts)
    return cache.get_or_compute(embedder, texts)
