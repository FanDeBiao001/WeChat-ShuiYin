# 轻影存后端更新

本目录包含当前 Render 服务实际运行的 Flask 后端。来源为
[FanDeBiao001/WeChat-ShuiYin](https://github.com/FanDeBiao001/WeChat-ShuiYin)，
基线提交 `44dc04cc4c793777be18155be6f257e64a675977`。保留原 MIT 许可。

## 登录

- `POST /api/auth/wechat`，请求 `{ "code": "wx.login返回的临时凭证" }`。
- 服务端向微信换取身份，只返回随机会话令牌、7天有效期和脱敏账号ID。
- `GET /api/auth/me` 验证 Bearer 会话；`POST /api/auth/logout` 撤销会话。
- 令牌仅以 SHA256 摘要存入 SQLite；AppSecret、session_key、openid 不返回客户端。
- Render Environment 必须配置 `WX_APP_ID`、`WX_APP_SECRET`。不要提交真实密钥。

## 性能

- 小程序解析成功结果缓存120秒、最多128条；只缓存底层解析数据，每次重新签发媒体地址。
- 同链接并发解析合并；每worker最多2次上游解析、4个解析请求（含等待者），超出返回429。
- 解析网络操作使用45秒预算、连接最长3秒、单次读取最长8秒。
- 下载复用线程内HTTP连接池，保留Range/206/416，HEAD不读取正文。
- 校验响应文件类型及200MB上限，下载中断/错误/客户端关闭时释放上游连接。
- 手动刷新传 `refresh: true` 跳过缓存；失败结果不缓存。
- `Server-Timing` 和无素材URL的日志记录解析耗时、缓存状态、下载首响应耗时。

## Render

现有服务继续使用原后端仓库，根目录保持为空：

```text
Build Command: pip install -r requirements.txt
Start Command: API_ONLY=true gunicorn -c gunicorn.conf.py app:app
Health Check Path: /health
```

启动配置是1个worker、8个线程，不增加实例规格。Render原命令中的`--threads 4`
会覆盖配置文件，因此上线时要使用上述启动命令。`/health`返回实际部署提交`revision`。

在Render运行时默认识别平台转发的客户端IP，避免所有登录请求共用代理IP限流桶；
其他环境需明确配置`TRUST_PROXY_HEADERS`，仅在可信反向代理后启用。

Free实例休眠唤醒无法通过本次代码优化消除。其本地SQLite和签名密钥文件会在实例
重启/重新部署后丢失，现有会话需要重新登录；本机素材按稳定账号ID存储，不会因此
被清空。长期保持会话需持久化数据库；如需升级实例或添加磁盘，应另行确认费用。

## 验证

```sh
API_ONLY=true DATABASE_PATH=/tmp/qingying-test.sqlite python -m unittest discover -s tests -p 'test_miniapp*.py'
```

测试中的微信身份和媒体上游使用桩，不代表真实微信授权或真实下载已通过。
上线后需额外检查健康提交、真实`wx.login`、素材库查看与退出登录。

回滚：Render选择上一部署；本次仅新增会话表，旧服务不会读取该表。
