> **归档文档 / Archived.** 此文档描述的是重构前的旧模型（独立的 `tweets` / `comments` / `retweets` 表），已不再反映当前代码。
> 当前项目说明见仓库根目录的 [`readme.md`](../../readme.md)。

# 推特系统

一个用于练习系统设计与后端实现的类 Twitter / 微博项目，重点覆盖：

- 发帖（Tweet）
- 关注 / 取关
- 主页时间线（Timeline）
- 点赞 / 评论
- 时间线高并发与大 V（celebrity problem）场景分析

---

## 1. 功能概览

### 已实现功能

- 发帖（类似 tweet）
- 关注 / 取关
- 主页时间线
- 点赞 / 评论

### 时间线重点

本项目重点讨论主页时间线的两种常见设计：

- **拉模式（fan-out on read）**
- **推模式（fan-out on write）**

---

## 2. 技术点

- 数据库设计（`users` / `tweets` / `follows` / `feed_items` 等）
- 分页设计（cursor pagination vs offset pagination）
- N+1 查询优化
- Redis 缓存
- 异步任务队列（RQ）
- 限流
- 大 V 高粉丝场景下的时间线分发

---

## 3. 系统设计核心问题

### 3.1 如何设计时间线？

#### 拉模式（fan-out on read）

用户请求时间线时，系统实时查询其关注用户的推文并聚合结果。

**特点：**

- 写入轻
- 读取重
- 更适合普通用户场景
- 当关注列表很大时，查询成本会上升

#### 推模式（fan-out on write）

用户发帖时，系统把该推文预写入所有粉丝的 `feed_items` 中。

**特点：**

- 写入重
- 读取轻
- 更适合普通粉丝读取频繁的场景
- 在大 V 场景下，写扩散成本很高

---

### 3.2 如何优化性能？

- Redis 缓存首页时间线
- 使用 cursor pagination 替代 offset pagination
- 建立符合访问模式的联合索引
- 聚合查询点赞数 / 评论数
- 避免 N+1 查询
- 使用异步任务处理高成本 fan-out

---

### 3.3 高并发怎么办？

- 异步任务（RQ / worker）
- 限流（rate limiting）
- 利用数据库唯一约束保证正确性
- 接口设计保持幂等
- 大 V 场景下考虑推拉混合策略

---

## 4. 实现过程中遇到的问题与思考

---

### 4.1 `response_model` 是做什么的？

- 校验响应数据
- 序列化响应数据
- 过滤不想暴露的字段
- 生成更准确的 OpenAPI / Swagger 文档

---

### 4.2 点赞 / 评论这类交互，如何处理高并发？

#### A. 使用数据库唯一约束

例如点赞可以对 `(user_id, tweet_id)` 建立唯一约束：

- 即使两个请求同时到达
- 数据库也能保证不会产生重复点赞

#### B. 接口要尽量幂等

例如点赞接口：

- 如果已经点过赞，不应该报 500
- 可以直接返回“已点赞”的成功结果

#### C. 增加限流

防止恶意刷接口或突发流量压垮系统。

#### D. 计数器异步更新

如果以后增加反范式字段，例如：

- `tweet.like_count`
- `tweet.comment_count`

可以考虑：

- 原子更新
- 异步更新

#### E. 评论需要分页与审核能力

评论列表本身也可能非常大，因此要支持：

- 分页
- 内容审核
- 后续扩展的风控能力

---

### 4.3 什么是 N+1 问题？

N+1 指的是：

- 先执行 1 次查询获取列表
- 然后对列表中的每一项再额外执行 1 次查询

例如：

- 先查 20 条 tweet
- 再为每条 tweet 查 author
- 总共变成 `1 + 20 = 21` 次查询

#### 怎么解决？

- 使用 `joinedload` / eager loading
- 在 SQL 中聚合点赞数、评论数
- 避免在 Python 循环中继续查数据库

---

### 4.4 cursor pagination 和 offset pagination 有什么区别？

#### Offset pagination

示例：

- 第 1 页：`offset 0 limit 20`
- 第 2 页：`offset 20 limit 20`

#### 问题

- 深分页会越来越慢
- 新数据插入后，容易跳过或重复
- 不适合大规模时间线场景

#### Cursor pagination

用上一页最后一条记录的排序键作为游标，例如：

- `(created_at, id)`

下一页就查询：

- 按相同顺序，取游标之后的数据

#### 为什么更适合 feed？

- 对插入更稳定
- 大数据量下性能更好
- 非常适合按时间倒序的场景

---

### 4.5 Redis 的 `get`、`setex` 看起来像 awaitable，为什么这里不需要 `await`？

这里只是类型提示或编辑器提示造成的误解。

本项目里使用的是：

- **同步 Redis client**

所以这里不需要 `await`。

---

### 4.6 现在 `run_feed_fanout_job()` 只是把 feed 批量写入数据库。大概到什么规模会出问题？RQ 能解决什么？Kafka 又是什么？

#### 当前实现的含义

当前 `run_feed_fanout_job()` 本质上是：

- 查出粉丝列表
- 批量插入 `feed_items`

#### 什么时候会开始出现问题？

当出现以下情况时：

- 单个用户粉丝量特别大（大 V）
- 发帖频繁
- 写扩散量极高
- cache invalidation 成本高
- 单个 worker 处理不过来

#### RQ 能解决什么？

RQ 可以把高成本操作移出请求链路：

- 用户发帖接口更快返回
- fan-out 在后台执行
- 失败可重试
- worker 可独立扩容

#### Kafka 是什么？

Kafka 更适合：

- 更大规模的异步消息分发
- 更高吞吐
- 更复杂的消费链路
- 更强的削峰填谷能力

在小项目 / 学习项目里，RQ 更轻量、更容易落地；
在真正的大规模分发系统里，Kafka 常常更合适。

---

### 4.7 为什么 `User` 模型需要这个 relationship？`back_populates` 和 `cascade` 是什么意思？

代码：

```python
tweets = relationship(
    "Tweet", back_populates="author", cascade="all, delete-orphan"
)
```

#### `back_populates`

表示双向关系映射：

- `User.tweets`
- `Tweet.author`

这两个属性互相对应。

#### `cascade="all, delete-orphan"`

控制 ORM 对子对象的联动行为。

##### `all`

会传播这些操作：

- save
- merge
- delete
- refresh 等

##### `delete-orphan`

如果子对象脱离父对象，而且没有其他父对象引用，SQLAlchemy 会把它删除。

#### 为什么这里合理？

因为在这个小项目里：

- tweet 必须属于某个用户
- 用户删除后，tweet 通常也应该删除

#### 生产环境的注意点

真实系统里删除用户往往没这么简单，因为可能涉及：

- 审计
- 合规
- 软删除
- 大规模级联删除成本

---

### 4.8 为什么 `Tweet` 模型要在这三个字段上建立联合索引？

代码：

```python
__table_args__ = (
    Index("ix_tweets_user_created_id", "user_id", "created_at", "id"),
)
```

#### 原因

典型查询模式是：

- 按作者过滤
- 按时间倒序排序
- 按 `(created_at, id)` 做游标分页

这个联合索引正好匹配查询路径。

#### 为什么还要加 `id`？

因为多个 tweet 可能有相同的 `created_at`，此时：

- `id` 可以作为稳定的第二排序键
- 保证分页结果稳定

---

### 4.9 为什么 `Feed` 模型索引建在 `owner_id` 上，而不是 `actor_id` 上？

#### 因为主页时间线的核心查询是：

- “给我看 **我的** 时间线”

也就是：

- `where owner_id = current_user`

#### 字段含义

- `owner_id`：谁在看这条 feed
- `actor_id`：谁发了这条 tweet

#### 什么时候 `actor_id` 才重要？

如果你的查询场景更多是：

- 看某个作者产生了多少 feed
- 做作者维度统计分析

那才可能考虑额外给 `actor_id` 建索引。

但对于主页时间线读取来说，`owner_id`` 才是最重要的。

---

### 4.10 `unfollow_user()` 里为什么不能简单用 `result.all()`？

有人可能会写成：

- `return len(result.all()) > 0`

这是不对的。

#### 原因

普通 `DELETE` 语句并不会返回数据行，除非你显式使用：

- `RETURNING`

因此：

- `result.all()` 不是判断删除是否成功的正确方式

这里更合理的判断方式是：

- `rowcount > 0`

如果类型检查器报错，通常只是 typing 层面的提示问题，不代表 SQL 语义错了。

---

### 4.11 `create_tweet()` 为什么需要 `db.refresh()`？

因为插入并提交之后，我们通常希望对象上已经拿到数据库生成的值，例如：

- `id`
- `created_at`
- 其他 server defaults

#### `refresh()` 做了什么？

- 重新从数据库加载对象最新状态

#### 为什么有用？

这样可以确保后续返回的数据一定是已持久化、且字段完整的。

---

### 4.12 `list_tweets_by_authors()` 里为什么是 `< cursor_created_at`，不是 `>`？`IN (...)` 会不会有性能问题？

代码示例：

```python
if cursor_created_at is not None and cursor_id is not None:
    stmt = stmt.where(
        or_(
            Tweet.created_at < cursor_created_at,
            and_(
                Tweet.created_at == cursor_created_at,
                Tweet.id < cursor_id,
            ),
        )
    )
```

#### 为什么是 `<`？

因为时间线通常是按：

- `created_at DESC`
- `id DESC`

排序。

也就是说，第一页是最新的数据；
下一页应该拿“更旧”的数据，所以条件必须是：

- `created_at < cursor_created_at`
- 或者在同一时间戳下 `id < cursor_id`

#### `IN (...)` 会不会有性能问题？

会，取决于规模。

- 在关注人数不大时通常没问题
- 当 `author_ids` 非常大时，`IN (...)` 会变重

这也是为什么：

- fan-out on read 不适合无脑用在大 V 或超大 follow list 场景

实际系统中常见的做法包括：

- 限制关注规模
- 走混合策略
- 使用预聚合 / 搜索引擎 / 专门的 feed 服务

---

### 4.13 什么是幂等（idempotent）？如何保证幂等？

#### 为什么重要？

在分布式系统里，经常会发生：

- 请求重试
- 网络超时
- 客户端重复提交

如果接口不是幂等的，就可能出现重复写入。

#### 如何保证幂等？

- 使用数据库唯一约束
- 写入前先检查是否已存在
- 对某些接口使用 idempotency key
- 删除接口即使目标不存在，也要安全返回

---

### 4.14 `list_feed_tweets()` 和 `list_tweets_by_authors()` 看起来很像，它们有什么区别？

它们服务于两种不同的时间线架构。

#### `list_tweets_by_authors()`

用于：

- **fan-out on read**

逻辑是：

- 先拿到用户关注的人
- 再实时查询这些作者的 tweet

#### `list_feed_tweets()`

用于：

- **fan-out on write**

逻辑是：

- 直接从预写好的 `feed_items` 中查当前用户的 timeline

#### 本质区别

- 一个是“读时聚合”
- 一个是“写时预计算”

---

### 4.15 为什么需要 schemas？命名像 `xxxCreate`、`xxxOut` 是惯例吗？

是的，这是一种非常常见的命名方式。

#### 为什么需要 schemas？

因为 API 请求 / 响应模型和数据库模型不应该完全混在一起。

schemas 的作用：

- 做输入校验
- 做输出序列化
- 防止暴露内部字段
- 明确接口契约
- 降低数据库模型与 API 层耦合

#### 常见命名方式

- `TweetCreate`
- `TweetUpdate`
- `TweetOut`

这是很常见、也很清晰的命名风格。

---

### 4.16 为什么 `get_db()` 要写成 generator？

这种写法的好处是：

- 请求开始前创建 session
- 请求处理时使用 session
- 请求结束后自动关闭 session

#### 为什么不直接 return？

如果只是简单返回 session：

- 清理逻辑更难统一管理
- 更容易出现连接泄漏

使用 generator + `yield` 的方式，框架可以在请求结束后自动执行清理逻辑。

---

### 4.17 为什么要在 Redis 里使用 Lua？

Lua 脚本在 Redis 中的价值主要有三点：

1. **原子性**  
   把多个操作打包成一个不可分割的执行单元，避免并发竞态。

2. **性能**  
   减少客户端与 Redis 之间的网络往返。

3. **复杂逻辑下沉**  
   可以直接在 Redis 端写条件判断、循环和组合操作，而不需要把数据来回传输给应用层。
