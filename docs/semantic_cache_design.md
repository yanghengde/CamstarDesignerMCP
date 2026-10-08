# Camstar Agent 语义缓存 (Semantic Cache) 设计方案

引入语义缓存（Semantic Cache）来过滤高频业务咨询是极其优秀的优化方向。它能将 LLM 的推理成本降至 0，并将响应时间从 6秒 压缩至 0.2秒 内。

但在一个**带有工具执行（MCP Tools）的 Agent 系统**中，粗放的缓存会带来灾难性后果。

## 一、为什么在 Agent 中搞缓存容易“翻车”？

1. **动作未执的假象 (Action Bypass)**
   - 场景：用户输入 _"帮我把 HD-02 的参数A改成100"_。
   - 错误：如果该语句命中缓存，系统会直接返回 _"好的，已修改完成"_，但**底层的 MCP Tool 并没有真正被调用**，MES/Camstar 里的数据根本没变。
2. **上下文依赖干扰 (Context Dependency)**
   - 场景：用户先问查列表，然后说 _"删除上面的第一条记录"_。这条极其简短的句子如果不带上文一起被缓存，极易导致错误命中。
3. **时效性过期 (Stale Data)**
   - 场景：用户问 _"查询当前产线最新状态"_。这虽然是查询（只读），但每次查询结果都应该是实时的。如果走了缓存，拿到的就是 1 小时前的数据。

---

## 二、如何做到“安全且精准”的缓存？

我们的核心原则是：**仅对「纯知识性、通用性」的闲聊和系统操作咨询进行缓存**，避开所有依赖实时状态和动作执行的指令。

推荐分为以下两个落地方向：

### 方案A：基于意图路由的前置缓存 (推荐)
1. **轻量级意图判别器**：
   在真正调用大模型之前，先用一个极快、极便宜的小模型（如 DeepSeek-Chat，或本地的小型 NLP 分类器），给用户的 `message` 打个标签：`[纯业务问答, 需要调用系统动作, 依赖上下文]`。
2. **分支处理**：
   - 仅针对 `[纯业务问答]` 走 Semantic Cache 检索。如果命中相似度 > 0.92，直接将结果 Streaming 给用户，流程瞬间结束。
   - 对于其他类型，正常流入现有的 `oai_client` 调用 MCP Tools 流程。

### 方案B：LLM 自主写入缓存池 (防呆设计)
通过把缓存操作做成**一种 Tool (工具)** 交给 LLM 自己调用。
- 你可以设计两个新的 MCP Tool：`search_knowledge_cache(query)` 和 `add_to_knowledge_cache(query, answer)`。
- LLM 在面对高频通用问题时，它可以主动调工具把标准解答写进库里；后来的用户再次询问时，系统也可以让 LLM 先快速查库。（这种方法不会绕过 LLM，但能帮 LLM 省去生成大量长篇通用分析的 token 时间）。

---

## 三、代码级实施示例 (以纯 Python + Numpy 实现极简 Semantic Cache)

如果采取**意图前置匹配**，下面提供一个可以直接在项目中运行的轻量级向量缓存类的代码设计思路。无需安装沉重的 VectorDB，几十万条以内的数据 `numpy` 即可应付。

```python
import os
import json
import numpy as np
from openai import AsyncOpenAI

# 这里假设依赖 openai 提供的 embedding 接口做向量化
oai_client = AsyncOpenAI(...)

class SimpleSemanticCache:
    def __init__(self, cache_file="data/semantic_cache.json", threshold=0.92):
        self.cache_file = cache_file
        self.threshold = threshold
        self.embeddings = []
        self.responses = []
        self.queries = []
        self.load_cache()

    def load_cache(self):
        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.queries = data.get("queries", [])
                self.responses = data.get("responses", [])
                self.embeddings = np.array(data.get("embeddings", []))
        else:
            self.embeddings = np.empty((0, 1536)) # 以 openai text-embedding-3-small 为例 (1536维)

    def save_cache(self):
        # 将内存写入 JSON 或其他持久化库
        data = {
            "queries": self.queries,
            "responses": self.responses,
            "embeddings": self.embeddings.tolist() if isinstance(self.embeddings, np.ndarray) else []
        }
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    async def get_embedding(self, text: str) -> np.ndarray:
        res = await oai_client.embeddings.create(
            input=text, 
            model="text-embedding-3-small"
        )
        return np.array(res.data[0].embedding)

    async def search(self, query: str):
        if len(self.embeddings) == 0:
            return None
        
        # 1. 获得当前提问的向量
        q_emb = await self.get_embedding(query)

        # 2. 余弦相似度计算 (假设向量已经归一化，可以通过 dot 乘积直接得出)
        similarities = np.dot(self.embeddings, q_emb)
        best_idx = np.argmax(similarities)

        # 3. 判断是否达到命中阈值
        if similarities[best_idx] >= self.threshold:
            print(f"✅ 命中语义缓存! 相似度: {similarities[best_idx]:.4f} (匹配语料: {self.queries[best_idx]})")
            return self.responses[best_idx]
        return None

    async def add_to_cache(self, query: str, response: str):
        q_emb = await self.get_embedding(query)
        self.queries.append(query)
        self.responses.append(response)
        if len(self.embeddings) == 0:
            self.embeddings = np.array([q_emb])
        else:
            self.embeddings = np.vstack([self.embeddings, q_emb])
        self.save_cache()
```

### 集成到 `llm_client.py` 里的形态
在你的 `chat_stream` 方法头部：

```python
# [前置规则判断] - 用简单的启发式判断这是否可能是一个动作
# 或者，直接给大模型起一个小副Agent来判断。如果断定是纯咨询，则去查找 Cache。
if is_pure_information_query(message): 
    cached_reply = await semantic_cache.search(message)
    if cached_reply:
         yield f"data: {json.dumps({'type': 'stream_start'}, ensure_ascii=False)}\n\n"
         yield f"data: {json.dumps({'type': 'stream_chunk', 'content': cached_reply}, ensure_ascii=False)}\n\n"
         yield f"data: {json.dumps({'type': 'stream_end'}, ensure_ascii=False)}\n\n"
         return  # 直接阻断，根本不需要给业务主 LLM 发消息
```
