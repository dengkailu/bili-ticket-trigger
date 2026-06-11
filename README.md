# Bili Ticket Trigger

B站会员购抢票工具。App 模式扫码登录 → 选票档 → dry-run 模拟 / 真实下单。

## 安装

```bash
git clone https://github.com/dengkailu/bili-ticket-trigger.git
cd bili-ticket-trigger
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

## 使用

```bash
python main.py
```

```
  B站会员购抢票工具
  登录: ✓ 用户名
  购票人: 1 人  代理: http://127.0.0.1:7890 (仅下单使用)  通知: 飞书
  ─────────────────────────────────────────
  [1] 扫码登录        [2] 验证登录状态
  [3] 购票人管理      [4] 项目查询
  [5] 监控票档        [6] 抢票
  [7] 接口诊断        [8] API逆向工程
  [9] 代理/通知配置    [0] 退出
```

回车默认返回上级菜单，空格/方向键多选购票人。

### 子命令

```bash
python main.py login                     # 扫码登录
python main.py info 1001405              # 项目详情
python main.py check 1001405             # 可购票档
python main.py monitor 1001405           # 监控
python main.py buy 1001405 877178        # dry-run 模拟
python main.py buy 1001405 877178 --real # 真实下单
python main.py reverse 1001405 --quick   # API逆向工程
```

## 功能

| 功能 | 说明 |
|------|------|
| App 模式 | Android UA + 设备指纹 + App 签名，模拟真实 App 请求 |
| 扫码登录 | 二维码 PNG 自动打开，B站 App 扫码获取 Cookie |
| 交互式 CLI | 颜色 + 清屏 + 复选框多选，无需记忆命令 |
| 购票人管理 | 本地 + B站 双向同步，自动匹配 buyer ID |
| 项目查询 | 场次/票档/价格/余量统一表格 |
| 票档监控 | 轮询检测，实时展示可购状态 |
| 抢票引擎 | dry-run 默认，`--real` 真实 prepare→createV2 下单 |
| id_bind 自适应 | 自动检测项目实名要求，匹配 B站 buyer ID |
| 限流策略 | 固定间隔重试，限流时自适应延迟，不退避 |
| 定时抢票 | `--sale-time` 开售前自适应等待 (15s→2s→0.1s→3ms) |
| 代理池 | 单节点/多节点轮转，仅下单使用 |
| 通知推送 | Telegram / 飞书，含支付链接 + 10 分钟截止提醒 |
| 逆向工具 | 差分测试 + 端点扫描 + 指纹发现 + 参数规格生成 |

## 抢票流程

```
扫码登录 → [3] 添加购票人(自动同步B站) → [4] 查项目选票档
  → dry-run 验证参数 → [9] 配代理/通知 → [6] --real 真实下单
  → 飞书/微信通知 → 点击链接付款
```

**默认 dry-run**，只打印完整 payload。加 `--real` 才真实下单。

## 项目结构

```
main.py          CLI 主程序（交互 + 子命令双模式）
bili_api.py      API 客户端（登录/查询/下单/重试引擎）
config.py        配置管理（代理池/通知）
notify.py        通知模块（Telegram/飞书）
checkbox.py      终端复选框选择器
reverse.py       API 逆向工程（差分测试 + 端点扫描）
probe.py         指纹参数探测器
discover.py      未知参数发现器
capture.py       抓包代理
api_spec.py      API 参数规格生成器
```

## 安全

以下文件含个人数据，已 `.gitignore`：

- `config.json` — Cookie 和登录态
- `buyers.json` — 购票人姓名/身份证/手机号

## License

MIT
