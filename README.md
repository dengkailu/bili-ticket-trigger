# Bili Ticket Trigger

B站会员购抢票工具。扫码登录 → 选票档 → dry-run 模拟 / 真实下单。

## 安装

```bash
git clone https://github.com/dengkailu/bili-ticket-trigger.git
cd bili-ticket-trigger
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
```

## 使用

### 交互式 CLI（推荐）

```bash
python main.py
```

```
┌  B站会员购抢票工具 ──────────────────────────┐
│  [1] 扫码登录        [5] 查看项目详情        │
│  [2] 验证登录状态    [6] 查看可购票档        │
│  [3] 管理购票人      [7] 监控票档            │
│  [4] B站实名观演人   [8] 抢票 (dry-run/真实) │
│  [9] 接口诊断        [A] 代理/通知配置        │
│  [0] 退出                                      │
└───────────────────────────────────────────────┘
```

### 子命令模式（脚本/自动化）

```bash
python main.py login                    # 扫码登录
python main.py buyer add                # 添加购票人
python main.py info 1001405             # 项目详情
python main.py check 1001405            # 可购票档
python main.py monitor 1001405          # 监控
python main.py buy 1001405 877178       # dry-run 模拟
python main.py buy 1001405 877178 --real # 真实下单
python main.py diagnose 1001405         # 接口参数诊断
```

## 功能

| 功能 | 说明 |
|------|------|
| 扫码登录 | 生成二维码 PNG 自动打开，B站 App 扫码提取 Cookie |
| 交互式菜单 | 无需记忆命令，全流程引导 |
| 项目查询 | 场次/票档/价格/发售时间/限购/余量 |
| 票档监控 | 轮询检测，实时展示可购状态 |
| 自动抢票 | dry-run 默认开启，`--real` 真实下单 |
| 下单链路 | prepare → createV2，自动获取 token |
| 指数退避 | "请慢一点"→500ms~2s 递增，"前方拥堵"→重试 |
| 定时抢票 | `--sale-time "2026-06-10 18:00:00"`，开售前自适应等待 |
| 接口诊断 | 逐字段探测必需/可选参数 + 格式敏感性 |
| 购票人管理 | 身份证校验、手机号管理、从B站拉取实名观演人 |
| 通知推送 | Telegram / 飞书 Webhook，抢票成功自动通知 |
| 代理支持 | HTTP/HTTPS 代理配置 |

## 抢票流程

```
扫码登录 → 添加购票人 → 查项目选票档 → dry-run 验证参数
  → 配置代理/通知 → --real 真实下单 → 通知推送
```

**默认 dry-run**，只打印 payload 不提交。加 `--real` 才真实下单。

## 安全

以下文件含个人数据，已 `.gitignore`：

- `config.json` — Cookie 和登录态
- `buyers.json` — 购票人姓名/身份证/手机号

## License

MIT
