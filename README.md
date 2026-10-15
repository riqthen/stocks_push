# A 股成交额通知

免费组合：GitHub Actions 定时取数，ntfy App 向 iPhone 推送通知。不需要保持 Windows 电脑开机。数据来自东方财富公开行情：沪市 A 股指数（000002）、深证 A 指（399107）的累计成交额，加上北交所股票行情逐只成交额汇总；不包括 ETF。该非官方接口可能限流、中断或调整口径，**不适合作为交易决策依据**。

## 启用

1. 在 iPhone 安装 [ntfy](https://ntfy.sh/) App，并允许通知。在 App 中订阅一个自行生成的随机主题，例如 `stocks-` 加上至少 24 位随机字母数字。不要使用示例主题；知道主题的人可以向该主题发消息并读取消息。
2. 在此仓库的 **Settings → Secrets and variables → Actions → New repository secret** 中创建 `NTFY_TOPIC`，值填上述主题名称（不要填网址）。不要把主题写入代码、README 或公开的 Issue。
3. 在 **Settings → Actions → General → Workflow permissions** 中启用 **Read and write permissions**，以便任务把每日快照提交到仓库。确认 **Actions** 已启用。
4. 推送代码到默认分支后，到 **Actions → A-share turnover notification** 查看定时执行情况。在交易日规定时段内可用 **Run workflow** 手动触发一次测试；时段外运行会跳过，不会发消息。

推送时刻（北京时间）：9:30、9:45、10:00、10:15、10:30、11:00、11:30、13:00、13:30、14:00、14:15、14:30、14:45、15:00。实际任务在对应时刻约 2 分钟后启动；GitHub Actions 定时任务可能延迟，超过 10 分钟将跳过该档。9:30 行情尚未更新时也会跳过。交易日由上交所日历判断。

每日快照保存在 `data/YYYY-MM-DD.json` 并提交回仓库；仓库若为公开状态，成交额快照也公开。首次运行当天没有前一交易日的同档快照。若两天的实际取数时间相差超过 5 分钟，则显示“暂无可比快照”，避免把不同时点误当同期。免费的 ntfy.sh 公共服务有使用限制，也不提供可靠送达保证。

## 本地检查

需要 Python 3.11+。安装依赖：`python -m pip install -r requirements.txt`；运行测试：`python -m unittest -v`。运行 `python push.py collect` 只会在交易时段取数并写入快照；`notify` 会真正发送通知，切勿公开 `NTFY_TOPIC`。
