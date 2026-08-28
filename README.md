# GMO FX Discord判断支援Bot

GMOクリック証券 FXネオの市場データ・口座状態・無料経済指標カレンダーを読み取り、GPT-5.6 Lunaでデイトレ〜短期スイング向けの判断を作りDiscordへ通知するBotです。

- **自動売買はしません。**
- GMO Private APIは参照GETだけを使用します。
- ニュース要約・Crypto・Trading Economicsは使用しません。
- 経済指標はAPI Key不要の無料週間JSONを使用します。
- スケジュール機能はBot内部に持ちません。既存cronから1回ずつ起動する前提です。
- 添付のGitHub Actions Workflowも **scheduleなし / workflow_dispatchのみ** です。外部cronからWorkflowを起動する構成にも対応します。

## 運用目的

基準資金40万円、デイトレ〜短期スイングを想定し、勝率単独ではなく年間純損益・Expectancy・Profit Factor・最大ドローダウンを重視します。

`TARGET_ANNUAL_RETURN_PCT=100` は評価目標であり、目標未達を理由にロットを自動増加させません。

## 予測ロジック

新規Entry用GPTプロンプトは、単なる「現在のトレンド説明」ではなく **今後4〜12時間の方向予測** を要求します。

- 4h = 大局とトレンド仮説
- 1h = 方向予測の主軸・セットアップ
- 15m = 約定タイミング
- `trend_score`: 4〜12時間先の方向予測と確信度（-1〜+1）
- `entry_quality`: 方向とは別に「提案された注文方法・価格で入る質」
- `entry_plan`: `ENTER_NOW` / `PULLBACK_LIMIT` / `BREAKOUT_STOP`
- `entry`: 推奨約定値
- `trend_invalidation`: **ここまで逆行すれば1h/4hの予測前提が崩れたとみなす逆指値水準**
- `take_profit`: 4〜12時間の最初の現実的な利確目標

High / Middle / Low の複数OCO案は出しません。**1回の分析につき注文案は1つだけ**です。

現在値追随より押し目・戻り待ちの期待値が高いと判断した場合は `PULLBACK_LIMIT` を返し、Discordに「押し目買いLIMIT」または「戻り売りLIMIT」と推奨約定値を明示します。ブレイク待ちが適切なら `BREAKOUT_STOP`、現在値付近が最善なら `ENTER_NOW` です。

逆指値はRRを良く見せるための機械的な狭いSLではなく、1h/4h構造・20本高安・SMA・ATRを使ったトレンド崩壊水準として出させます。先に崩壊水準を決め、その後に利確目標を決めます。現実的なRRが不足する場合は無理に数値を作らずEntry Qualityを下げ、Python側でも見送ります。

### Python側の予測検証

GPTの出力はそのまま採用しません。

- 4h方向との整合
- Entry / 崩壊逆指値 / TPの価格順序
- 最低RR
- 逆指値が15m ATRに対して近すぎないこと
- Entryが現在値から離れすぎていないこと
- tickSize丸め後の整合
- trend_score閾値
- entry_quality閾値

をPython側で再検証します。

## 既存建玉・未約定注文

新規Entry分析とは別のGPTバッチで管理します。

### 建玉あり

基本アクション:

- `HOLD`
- `CLOSE`
- `TAKE_PARTIAL`
- `TIGHTEN_SL`
- `REVIEW_MANUALLY`

保有継続の場合は `trend_invalidation` を通知し、**トレンドが変わったと判断する逆指値水準**を確認できます。既存STOPを損失側へ広げる提案はPython側で拒否します。

### 未約定注文あり

基本アクション:

- `KEEP_ORDER`
- `CANCEL_ORDER`
- `REPRICE_ORDER`
- `REVIEW_MANUALLY`

注文を維持・価格変更する場合は **`recommended_order_price`（推奨約定値）を必須** としています。

Discordには、

```text
現在注文価格
推奨約定値
KEEP / REPRICE / CANCEL判断
```

を表示します。

BUY LIMITなのに現在Askより上、SELL STOPなのに現在Bidより上など、注文種別と矛盾する推奨価格はPython側で拒否します。また、GPTがKEEPと返しても現在注文価格と推奨約定値の差が大きければ `REPRICE_ORDER` へ補正します。

## 主な仕様

- 対象は `symbols.csv` に書いたGMO FXネオ取扱銘柄のみ
- 初期対象12銘柄
- 未確定ローソク足を除外
- Wilder RSI / ATR / ADX、MACD、SMA、DI、ボラティリティ等をPython計算
- 4hがTREND_UP/TREND_DOWNでない場合は原則新規Entry見送り
- 15m逆行は押し目/戻り候補として許容
- Entry/崩壊逆指値/TPをGMO `tickSize` に丸める
- 口座EquityとEntry〜崩壊逆指値距離から推奨数量を算出
- 合計リスク・通貨集中リスクを候補ごとに累積管理
- 複数候補は品質順にRisk Budgetを割当
- 証拠金維持率、保護STOP不足、Spread/ATR等をハードフィルター
- 全判断・Equity・約定履歴をSQLiteへ保存
- BotのEntry候補を仮想トレード追跡し、WIN/LOSS/MFE/MAE/Rを記録
- 無料High Impact経済指標の事前/直前警告
- 指標前後は該当通貨の新規Entry停止
- 無料カレンダー障害時はキャッシュ、キャッシュも無効なら安全側でEntry停止

## 初期対象銘柄

```text
AUD_JPY
EUR_JPY
USD_JPY
GBP_JPY
AUD_USD
EUR_USD
GBP_USD
NZD_JPY
CAD_JPY
NZD_USD
EUR_GBP
USD_CHF
```

`symbols.csv` は増減可能です。起動時にGMO `/symbols` と照合し、GMO非対応symbolがあれば停止します。

## 必須のAPI Key / Secrets

```bash
OPENAI_API_KEY=...
GMO_FX_API_KEY=...
GMO_FX_API_SECRET=...
DISCORD_FOREX_MAIN=...
DISCORD_FOREX_OTHER=...
```

任意:

```bash
DISCORD_FOREX_EVENT=...
```

未設定なら重要指標通知はMAINへ送ります。

**ニュース・経済指標用API Keyは不要です。**

### GMO API Key権限

このBotはPrivate APIを読み取り専用で使用します。口座情報、建玉、有効注文、約定履歴などの参照権限だけを付けてください。

新規注文、決済、変更、取消のPOST処理はコードに実装していません。発注系権限は不要です。

## OpenAI設定

既定:

```bash
OPENAI_MODEL=gpt-5.6-luna
OPENAI_MARKET_REASONING_EFFORT=medium
OPENAI_MANAGEMENT_REASONING_EFFORT=medium
OPENAI_BATCH_ANALYSIS=true
OPENAI_BATCH_MAX_SYMBOLS=6
```

予測品質を優先し、Lunaのreasoningは従来の`low`から`medium`を既定に変更しています。必要なら環境変数で`low`へ戻せます。

新規Entryと建玉/注文管理は別プロンプトです。バッチと小分けでDiscord通知ロジックは共通です。

## 初期リスク設定

```bash
BASE_CAPITAL_JPY=400000
TARGET_ANNUAL_RETURN_PCT=100
RISK_PER_TRADE_PCT=0.75
MAX_TOTAL_RISK_PCT=2.5
MIN_MARGIN_RATIO=300
MIN_RR=1.5
MAX_CURRENCY_EXPOSURE_RISK=2.0
MAX_SPREAD_ATR_RATIO=0.12
ENTRY_SCORE_THRESHOLD=0.65
ENTRY_QUALITY_THRESHOLD=0.68
```

これらは実証済み最適値ではありません。SQLiteの仮想シグナル実績と実口座成績を蓄積して調整する前提です。

## 無料経済指標カレンダー

既定URL:

```text
https://nfs.faireconomy.media/ff_calendar_thisweek.json
```

API Key不要です。外部無料フィードのため、HTTP成功でも古い週データなら不採用とし、有効キャッシュへフォールバックします。

## ローカル / cron実行

Python 3.12推奨。

```bash
python -m pip install -r requirements.txt
python validate_setup.py --symbols_file symbols.csv
python run_bot.py --symbols_file symbols.csv
```

`run_bot.py` は1回処理して終了します。時刻制御は既存cron側の責務です。

## GitHub Actions対応

`.github/workflows/api_check.yml` を同梱しています。

- `workflow_dispatch` のみ
- **GitHub Actions側にscheduleはありません**
- 外部cronから既存方法でWorkflowを起動可能
- Python 3.12
- `requirements.txt` を使用
- pip cacheを使用
- `state/` を `actions/cache` で前回実行から復元

GitHub-hosted runnerは毎回ファイルシステムが初期化されるため、SQLite・仮想トレード・通知重複状態・経済指標キャッシュを維持するには `state/` の復元が必要です。このWorkflowではrunごとに新しいcache keyを作り、次回は直近のstate cacheをrestoreします。

GitHub Repository Secretsには最低限以下を登録してください。

```text
OPENAI_API_KEY
GMO_FX_API_KEY
GMO_FX_API_SECRET
DISCORD_FOREX_MAIN
DISCORD_FOREX_OTHER
```

任意:

```text
DISCORD_FOREX_EVENT
```

## Discord通知

### 新規候補

- 4〜12h方向予測
- trend score
- Entry Quality
- **推奨約定値**
- **トレンド崩壊逆指値**
- 利確目標
- RR
- 推奨数量
- 想定損失

### 未約定注文

- 現注文価格
- **推奨約定値**
- KEEP / REPRICE / CANCEL
- Confidence

### 保有中

- HOLD / CLOSE / 部分利確 / SL引上げ
- 現在の保護逆指値
- **トレンド崩壊逆指値**

`DISCORD_FOREX_MAIN` は行動が必要な通知、`DISCORD_FOREX_OTHER` は全分析・見送りログとして利用します。

## 永続データ

デフォルト:

```bash
BOT_STATE_DIR=state
BOT_STATE_DB=state/fxbot.sqlite3
CALENDAR_CACHE_PATH=state/ff_calendar_cache.json
```

ローカルcronでは消えないパスを推奨します。GitHub Actionsでは同梱Workflowが `state/` をcacheします。

## 成績確認

```bash
python report_performance.py
```

主な表示:

- 仮想シグナル勝率
- 総R
- Expectancy
- Profit Factor
- 最大DD(R)
- 平均MFE / MAE
- 銘柄別成績
- GMO同期済み決済損益
- 口座Equity推移/DD

## セットアップ診断

```bash
python validate_setup.py --symbols_file symbols.csv
```

OpenAI Keyの存在、Discord設定、GMO Public/Private参照、対象銘柄、無料経済指標カレンダーを確認します。

**OpenAI課金リクエスト、Discord送信、GMO注文は行いません。**

## テスト

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```
