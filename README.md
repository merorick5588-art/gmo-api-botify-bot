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

予測対象は最新Bidに対する約8時間後のBID終値方向を中心とし、4時間後と12時間後でも根拠が維持されるかを確認します。期間内に一度触れる目標価格と、将来の終値方向を区別します。この8時間という中心点は評価対象を明確にする設計上の選択であり、最適化した結果ではありません。

予測入力には、各時間足の基準終値、4/12本の純変化÷ATR、RSI・MACDヒストグラムの3本変化、最新足を除いた直前20本高安に対する終値ブレイク距離を含めます。これらは現在水準に加えて継続・失速を判断するための情報であり、精度向上を実測済みという意味ではありません。完成足終了時刻とBid/Ask時刻も渡します。入力は通常の `run_bot.py` 実行で再生成されます。

- 1d = 長期背景（売買トリガーにはしない）
- 4h = 大局とトレンド仮説
- 1h = 方向予測の主軸・セットアップ
- 15m = 約定タイミング
- `trend_score`: 4〜12時間先の方向に関する未校正の判断スコア（-1〜+1）。勝率ではありません。
- `entry_quality`: 方向とは別に「提案された注文方法・価格で入る質」
- `entry_plan`: `ENTER_NOW` / `PULLBACK_LIMIT` / `BREAKOUT_STOP` / `NO_TRADE`
- `entry`: 推奨約定値
- `trend_invalidation`: **ここまで逆行すれば1h/4hの予測前提が崩れたとみなす逆指値水準**
- `take_profit`: 4〜12時間の最初の現実的な利確目標

High / Middle / Low の複数OCO案は出しません。**1回の分析につき注文案は最大1つ**です。方向不明・根拠不足・合理的な注文が作れない場合は `NO_TRADE` を返します。その場合は `entry_quality=0`、価格3項目は `null` で、Discordの見送り通知へ進みます。

現在値追随より押し目・戻り待ちの期待値が高いと判断した場合は `PULLBACK_LIMIT` を返し、Discordに「押し目買いLIMIT」または「戻り売りLIMIT」と推奨約定値を明示します。ブレイク待ちが適切なら `BREAKOUT_STOP`、現在値付近が最善なら `ENTER_NOW` です。

逆指値はRRを良く見せるための機械的な狭いSLではなく、1h/4h構造・20本高安・SMA・ATRを使ったトレンド崩壊水準として出させます。先に崩壊水準を決め、その後に利確目標を決めます。現実的なRRが不足する場合は `NO_TRADE`。旧形式のRR不足案や中立スコアもPython側で見送りに変換し、再問い合わせはしません。

特徴量の距離は各時間足の最新BID完成足終値が基準です。最新Bid/Askとは区別し、相関するSMA・MACD等の重複加点を避けるよう指示しています。スコアの採点目安は統計的に校正した確率ではありません。プロンプト改訂による収益性・精度向上は未検証です。

### v3: 仕組みの検証と予測監査

このBotは4h順張りの判断支援で、あらゆる相場の方向を自由に予測するモデルではありません。選別通過を予測の根拠に使わないこと、反対方向ならスコアを保持して見送ることをGPTに指示しています。新規予測にも12時間以内の既知の重要指標予定を渡し、発表結果の方向は推測させません。意味検証に失敗した注文案も再問い合わせせず、ERROR扱いで監査記録に残します。

`forecast_audit` テーブルを既存DBに自動追加します。LLM対象になった全候補について、市場入力、モデル名、プロンプトのSHA-256、検証後の結果（見送りを含む）を保存します。失敗はERRORとして保存し、生の不正応答は保存しません。口座情報・APIキーはこのテーブルへ渡しません。

次回の実行または `python report_performance.py` で8時間後の方向を評価します。8時間後以降の最初の完成済み15m BID終値（最大15分のずれ）を使用し、対象時刻の足が取得できなければ24時間待った後MISSINGにします。別日の価格で埋めません。レポートはモデル・プロンプト別に的中率、見送り数、失敗数、欠測数、同じ方向評価対象での単純4h順張りの的中率を表示します。

非ゼロの方向スコアは見送りでも評価し、中立は方向的中率から除外します。対象は事前選別後の候補だけであり、全相場の精度ではありません。短い間隔の予測は評価期間が重複するため独立標本ではありません。過去の入力・見送り記録を復元できないため、v3以降の観測から評価します。定期実行が長く止まるとローリングCSVから対象足が失われ、欠測になります。

新規予測には再取得した最新tickerを使用し、15m/1h完成足が未来または古い場合は見送ります。短期足の遅延許容は各時間足の2本分+3分です。以前の入力JSONには時刻がないため、コード更新後は通常の `run_bot.py` で必ず再生成してください。

仮想注文は期限後の足で約定させません。期限をまたぐ足は約定の前後関係が不明なので対象から外す保守的な扱いです。仮想TP/SL成績は8時間方向精度とは別で、追跡中・AMBIGUOUSを除く条件付き成績です。

### Python側の予測検証

v4ではGPT応答後にもtickerを再取得し、売買候補の価格・ATR条件を再検証します。最新価格を確認できない場合や注文条件が崩れた場合は見送ります。8時間予測の監査基準価格は予測時点のまま保持します。

数量計算は、有限かつ正の価格・資金と `minOpenOrderSize` / `maxOrderSize` / `sizeStep` を必須にします。欠けた数量ルールを推測して補いません。口座APIのページ取得が上限に達した場合やカーソルが進まない場合は、部分取得を成功扱いにせず失敗とします。管理提案のNaN・無限大・不正な部分利確率は手動確認に変換します。

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

## OHLC履歴本数と長期特徴量

既定を各時間足 `OHLC_TARGET_BARS=320` 本へ拡張し、15m/1h/4hに加えて **1day（日足）** も取得します。GMOのKLineは日足を年指定で取得できるため、API課金を増やさず長期背景を利用できます。

単に古いローソク足をGPTへ大量送信することはしません。1h/4h/1dでは、長期履歴から以下を圧縮特徴量として追加します。

- 全時間足: 100本高安までのATR距離、現在ATR%の過去最大250本内分位 (`atrq`)、50本Efficiency Ratio (`er50`)、直近50本でClose>SMA50の割合 (`p50`)
- 1h / 4h / 1d: SMA100 / SMA200からのATR距離と傾き、250本高安までのATR距離

日足は新規Entryのハードブロックには使わず、4h方向に対する長期の追い風/逆風としてGPTの `trend_score` / `entry_quality` に反映させます。これにより、単純な履歴本数増加ではなく実際に4〜12時間予測へ使える情報を増やしています。

EUR/GBPのように取扱開始が新しく320本の日足を取得できない銘柄は、**日足が100本以上あれば取得可能分を長期背景として利用**します。SMA200や250本高安など履歴が足りない特徴量は省略し、0埋めや推定はしません。4h/1h/15mは従来どおり260本以上を必須とします。4h/1dayの年指定取得は `OHLC_MAX_YEARS`（既定5年）の範囲で必要本数まで遡り、取扱開始前の年が404になった新規銘柄はそこで遡及を停止します。

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
MIN_MARGIN_RATIO=150
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

## OpenAI出力の安全対策

GPT-5.6 Lunaの `max_output_tokens` にはreasoning tokenも含まれます。medium reasoningでStructured OutputsのJSONが途中切断されないよう、新規Entry/建玉管理とも十分な出力余白を確保しています。JSONが途中切断された場合だけ、上限を拡張して1回自動再試行します。通常時にAPI呼び出し回数は増えません。

新規Entry候補が0件の場合は、Stage1で除外された各銘柄の理由を標準出力にも表示します。

## GitHub Actions対応

`.github/workflows/api_check.yml` を同梱しています。

- `workflow_dispatch` のみ
- **GitHub Actions側にscheduleはありません**
- 外部cronから既存方法でWorkflowを起動可能
- Python 3.12
- `requirements.txt` を使用
- pip cacheを使用
- Node.js 24対応の公式Actionを使用 (`actions/checkout@v6`, `actions/setup-python@v7`, `actions/cache@v5`)
- `state/` を `actions/cache@v5` で前回実行から復元

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
