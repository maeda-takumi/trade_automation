# kabuステーションAPI 自動取引システム 設計書（v0.1）

> 対象：kabuステーションAPI を用いたデイトレード（当日建て→当日決済）自動売買  
> 目的：複数銘柄の一括エントリー、TP/SL（自前OCO）監視、引け30分前強制決済、予約実行を安全に運用できること

---

## 0. 前提・スコープ

### 0.1 前提
- kabuステーションAPI はローカルホストで動作（例：`http://localhost:18080/kabusapi`）
- 本システムは「**エントリー約定後**」に、約定数量に応じた **利確（TP）** と **損切（SL）** の決済注文を発注し、片方約定で他方取消する **自前OCO** を提供する
- デイトレ前提：当日中に必ず決済（引け30分前時点で未決済なら成行決済）
- 予約実行：指定時刻にバッチ（複数銘柄注文）を開始する

### 0.2 非スコープ（v0.1）
- 複雑な戦略（指標、板解析、機械学習、複数エントリーなど）
- 複数口座同時運用（将来拡張で可能な設計にはする）
- リアルタイムWebSocket（APIがポーリング中心のため、まずはポーリング）

---

## 1. システム構成

### 1.1 構成（推奨）
- **UI（Web）**：管理画面（設定、銘柄リスト、バッチ作成、実行状況、ログ）
- **バックエンド（API/ジョブ）**
  - Scheduler：予約実行
  - Execution Engine：エントリー発注
  - Watcher：注文/建玉監視（ポーリング）
  - OCO Manager：TP/SLの相互キャンセル
  - EOD Closer：引け30分前の強制決済
- **DB**：SQLite（単体運用）または MySQL（複数端末/将来拡張）

### 1.2 主要コンポーネント責務
- **Scheduler**
  - 指定時刻に `batch_job` を `RUNNING` へ遷移し、エンジンを起動
- **Execution Engine**
  - `batch_items` を順にエントリー発注（レート制御）
  - 発注結果（OrderId）をDBへ記録
- **Watcher**
  - `/orders` `/positions` を定期取得し、DB状態を更新
  - 約定（部分/全）を検知し、Fill を記録
- **OCO Manager**
  - 約定数量に応じた TP/SL を発注
  - TP約定→SL取消 / SL約定→TP取消
- **EOD Closer**
  - 引け30分前に未決済を成行決済、残OCOは取消

---

## 2. 画面（UI）設計

> UIは「運用者が事故らず、現在地が分かる」を最優先  
> 画面はPC前提（レスポンシブは後回しでもOK）

### 2.1 画面一覧
1. **ログイン / セッション**
2. **ダッシュボード**
3. **銘柄リスト（ウォッチリスト）**
4. **バッチ作成（注文セット作成）**
5. **バッチ詳細（実行状況・OCO状態・手動操作）**
6. **スケジュール（予約一覧）**
7. **注文・約定一覧**
8. **ログ（イベント/エラー/HTTP）**
9. **設定（API・取引・通知）**

---

### 2.2 各画面詳細

#### 2.2.1 ダッシュボード
- 目的：稼働状況の一目把握
- 表示
  - 今日のバッチ：`SCHEDULED / RUNNING / DONE / ERROR`
  - 未決済数（現物/信用別）
  - 直近エラー（最新10件）
  - 引け強制決済の残り時間（営業日判定含む）
- 操作
  - 「新規バッチ作成」
  - 「緊急停止」（全RUNNINGバッチを停止→残OCO取消→必要なら決済）

#### 2.2.2 銘柄リスト（ウォッチリスト）
- 目的：一括注文に使う銘柄を管理
- 一覧列
  - 銘柄コード、市場、デフォルト売買（買/売）、現物/信用、デフォルト数量
  - デフォルトTP/SL（価格または％）
  - 有効/無効
- 操作
  - 追加/編集/削除
  - CSVインポート/エクスポート（将来）

#### 2.2.3 バッチ作成（注文セット）
- 目的：複数銘柄をまとめて発注する単位（=Batch）
- 入力
  - バッチ名（例：朝イチブレイク）
  - 実行種別：即時 / 予約（時刻指定）
  - 共通設定（任意）：  
    - 取引種別（現物/信用）  
    - 方向（買/売）  
    - TP/SL設定方式（価格 / ％ / ティック）  
    - EOD強制決済（ON/OFF、時刻）
- 明細（複数行）
  - 銘柄、数量、エントリー方式（成行/指値）、エントリー価格（指値の場合）
  - TP価格、SLトリガー価格（逆指値）
  - （信用の場合）返済方針：建玉指定（HoldID）/ 自動割当（実装は自動割当＝positionsから取得）
- 操作
  - バリデーション（価格関係、数量、同一銘柄重複など）
  - 保存、保存して実行、保存して予約

#### 2.2.4 バッチ詳細（最重要）
- 目的：実行中/実行後の状態確認と手動介入
- 上部サマリー
  - ステータス：SCHEDULED/RUNNING/PAUSED/DONE/ERROR
  - 実行開始/終了
  - ルール：引け強制決済時刻、OCO有効
- 明細テーブル（銘柄ごと）
  - エントリー注文：注文ID、状態、注文数量、約定数量、平均約定単価
  - TP注文：注文ID、状態、数量
  - SL注文：注文ID、状態、数量、トリガー
  - 現在の建玉（現物保有/信用建玉）
  - 次アクション（例：TP約定待ち、SL約定待ち、EOD待ち）
- 手動操作
  - TP取消 / SL取消
  - 成行決済（即時）
  - バッチ停止（以降の新規発注停止、残OCO整理）

#### 2.2.5 スケジュール（予約一覧）
- 一覧
  - 実行予定日時、バッチ名、状態、作成者
- 操作
  - 有効/無効
  - 予約変更
  - 予約削除

#### 2.2.6 注文・約定一覧
- フィルタ：日付、銘柄、バッチ、状態
- 表示：注文ID、種別（entry/tp/sl/eod）、数量、価格、約定情報

#### 2.2.7 ログ
- 種類：EVENT / ERROR / HTTP / AUDIT
- 重要：エラーは「再現に必要な情報」を必ず残す  
  例）endpoint、request body hash、status code、response（機密はマスク）

#### 2.2.8 設定
- API設定：APIパスワード、接続先URL、レート制限
- 取引設定：
  - 引け強制決済 ON/OFF、時刻（デフォルト 14:30）
  - 部分約定時のOCO方針：  
    - (A) 約定分ごとにTP/SL追加発注（推奨）  
    - (B) 全約定後にTP/SL発注（簡易）
- 通知設定（将来）：Slack/ChatWork/メール

---

## 3. 機能設計

### 3.1 機能一覧（大分類）
1. 認証・権限（最低限）
2. ウォッチリスト管理
3. バッチ作成・編集・複製
4. 予約実行（Scheduler）
5. 一括エントリー（Execution）
6. 自前OCO（TP/SL）
7. 監視（Watcher）
8. 片約定時の相互取消
9. 引け30分前 強制決済（EOD）
10. 緊急停止・手動介入
11. ログ・監査

---

### 3.2 主要ユースケース

#### UC-01：バッチを予約実行する
- 入力：バッチ（銘柄リスト、TP/SL、実行時刻）
- 処理：
  1) `batch_job` を `SCHEDULED` として保存
  2) Schedulerが実行時刻に `RUNNING` へ
  3) Executionが `batch_items` を順にエントリー発注
- 出力：バッチ詳細に注文IDが反映

#### UC-02：エントリー約定後にTP/SLを出す（自前OCO）
- 処理：
  1) Watcherがエントリー注文の約定（CumQty増加）を検知
  2) 約定数量ぶんの決済注文を発注
     - 現物：反対売買の注文
     - 信用：`positions` から建玉（HoldID）を取得して返済注文に紐づける
  3) `oco_group` として TP/SL を関連付け
- 出力：TP/SL注文IDが `batch_items` に反映

#### UC-03：TP約定→SL取消（または逆）
- 処理：
  1) Watcherが TP の全約定を検知
  2) OCO Manager が SL を取消
  3) `batch_item` を `CLOSED` に
- 例外：
  - 取消失敗（すでに失効/約定など）→状態を確認し整合を取る

#### UC-04：引け30分前に未決済を成行決済
- トリガ：当日 `EOD_CLOSE_TIME` 到達
- 処理：
  1) 未決済の `batch_items` を抽出
  2) 残TP/SLを取消
  3) 残建玉を成行で決済（信用はHoldID指定）
  4) `EOD_CLOSED` へ
- 出力：強制決済結果がログに残る

---

### 3.3 エンジン設計（状態機械）

#### 3.3.1 バッチ状態（batch_job.status）
- `SCHEDULED`：予約待ち
- `RUNNING`：稼働中
- `PAUSED`：手動一時停止
- `DONE`：全銘柄完了
- `ERROR`：異常終了
- `CANCELLED`：取り消し（予約削除/停止）

#### 3.3.2 銘柄状態（batch_item.status）
- `READY`：未発注
- `ENTRY_SENT`：エントリー発注済
- `ENTRY_PARTIAL`：部分約定あり
- `ENTRY_FILLED`：全約定
- `BRACKET_SENT`：TP/SL（OCO）発注済
- `TP_FILLED`：利確完了
- `SL_FILLED`：損切完了
- `EOD_MARKET_SENT`：引け成行決済発注済
- `CLOSED`：決済完了
- `ERROR`：例外

---

### 3.4 重要ルール（安全設計）
- **レート制御**：発注/取消は最小間隔を設ける（発注系を優先し過負荷を避ける）
- **冪等性**：同じ状態遷移を再実行しても壊れないように、DB側に「すでに発注済み」を保持
- **復旧性**：プロセス再起動後も、DBの `order_id` から現在状態を再構築できる
- **監査ログ**：手動操作（取消/決済/停止）は必ず `audit_log` に残す

---

## 4. テーブル設計（DB）

> SQLite/MySQL共通で使える設計（型はMySQL寄り表記）  
> 重要テーブル：`batch_jobs` / `batch_items` / `orders` / `fills` / `oco_groups` / `event_logs`

### 4.1 ER（概要）
- users 1—N batch_jobs
- batch_jobs 1—N batch_items
- batch_items 1—N orders（entry/tp/sl/eod）
- orders 1—N fills
- batch_items 1—N oco_groups（部分約定で複数OCOを持てる）
- oco_groups 1—1 tp_order / 1—1 sl_order（ordersに紐づく）

---

### 4.2 テーブル定義

#### 4.2.1 users（任意：単独運用なら省略可）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓|ユーザーID|
|email|VARCHAR(255)| |ログインID|
|password_hash|VARCHAR(255)| |パスワード（導入するなら）|
|role|VARCHAR(50)| |admin/operator|
|created_at|DATETIME|✓||
|updated_at|DATETIME|✓||

インデックス：`UNIQUE(email)`

---

#### 4.2.2 api_accounts（API接続設定：単独運用なら1行）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|name|VARCHAR(100)|✓|表示名|
|base_url|VARCHAR(255)|✓|例：http://localhost:18080/kabusapi|
|api_password_enc|TEXT|✓|APIパスワード（暗号化推奨）|
|is_active|TINYINT|✓|1=有効|
|created_at|DATETIME|✓||
|updated_at|DATETIME|✓||

インデックス：`(is_active)`

---

#### 4.2.3 watch_symbols（ウォッチリスト）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|symbol|VARCHAR(10)|✓|銘柄コード|
|exchange|INT|✓|市場コード|
|default_product|VARCHAR(10)| |cash/margin|
|default_side|VARCHAR(10)| |buy/sell|
|default_qty|INT| |デフォルト数量|
|default_entry_type|VARCHAR(10)| |market/limit|
|default_tp_mode|VARCHAR(10)| |price/percent/tick|
|default_tp_value|DECIMAL(16,4)| |TP値|
|default_sl_mode|VARCHAR(10)| |price/percent/tick|
|default_sl_value|DECIMAL(16,4)| |SL値（トリガー）|
|is_enabled|TINYINT|✓|1=有効|
|created_at|DATETIME|✓||
|updated_at|DATETIME|✓||

インデックス：`UNIQUE(symbol, exchange)`, `(is_enabled)`

---

#### 4.2.4 batch_jobs（バッチヘッダ）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|batch_code|VARCHAR(50)|✓|表示/検索用（例：20260209-090001）|
|user_id|BIGINT FK| |作成者|
|api_account_id|BIGINT FK|✓|接続先|
|name|VARCHAR(200)|✓|バッチ名|
|status|VARCHAR(30)|✓|SCHEDULED/RUNNING/...|
|run_mode|VARCHAR(20)|✓|immediate/scheduled|
|scheduled_at|DATETIME| |予約実行時刻|
|started_at|DATETIME| |実行開始|
|finished_at|DATETIME| |実行終了|
|eod_close_time|TIME|✓|引け強制決済時刻（既定14:30）|
|eod_force_close|TINYINT|✓|1=ON|
|note|TEXT| |メモ|
|created_at|DATETIME|✓||
|updated_at|DATETIME|✓||

インデックス：`UNIQUE(batch_code)`, `(status, scheduled_at)`, `(api_account_id, created_at)`

---

#### 4.2.5 batch_items（バッチ明細：銘柄ごと）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|batch_job_id|BIGINT FK|✓||
|symbol|VARCHAR(10)|✓||
|exchange|INT|✓||
|product|VARCHAR(10)|✓|cash/margin|
|side|VARCHAR(10)|✓|buy/sell|
|qty|INT|✓|発注数量|
|entry_type|VARCHAR(10)|✓|market/limit|
|entry_price|DECIMAL(16,4)| |指値価格|
|tp_price|DECIMAL(16,4)|✓|利確価格（最終的に価格へ展開して保存）|
|sl_trigger_price|DECIMAL(16,4)|✓|損切トリガー（逆指値）|
|status|VARCHAR(30)|✓|READY/ENTRY_SENT/...|
|filled_qty|INT|✓|累計約定数量|
|avg_fill_price|DECIMAL(16,4)| |平均約定単価|
|entry_order_id|VARCHAR(64)| |API OrderId|
|last_error|TEXT| |直近エラー|
|created_at|DATETIME|✓||
|updated_at|DATETIME|✓||

インデックス：`(batch_job_id)`, `(symbol, exchange)`, `(status)`

---

#### 4.2.6 orders（発注記録：entry/tp/sl/eod）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|batch_item_id|BIGINT FK|✓||
|order_role|VARCHAR(10)|✓|entry/tp/sl/eod|
|api_order_id|VARCHAR(64)|✓|API OrderId|
|side|VARCHAR(10)|✓|buy/sell|
|qty|INT|✓||
|order_type|VARCHAR(20)|✓|market/limit/stop|
|price|DECIMAL(16,4)| |指値価格|
|stop_trigger|DECIMAL(16,4)| |逆指値トリガー|
|status|VARCHAR(30)|✓|NEW/WORKING/PARTIAL/FILLED/CANCELLED/EXPIRED/REJECTED|
|cum_qty|INT|✓|累計約定|
|avg_price|DECIMAL(16,4)| |平均約定|
|sent_at|DATETIME|✓|発注時刻|
|last_sync_at|DATETIME| |最後にAPI照会した時刻|
|raw_json|JSON/TEXT| |APIレスポンス/注文詳細|
|created_at|DATETIME|✓||
|updated_at|DATETIME|✓||

インデックス：`UNIQUE(api_order_id)`, `(batch_item_id, order_role)`, `(status)`

---

#### 4.2.7 fills（約定履歴）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|order_id|BIGINT FK|✓|orders.id|
|fill_time|DATETIME|✓|約定時刻|
|fill_qty|INT|✓|約定数量|
|fill_price|DECIMAL(16,4)|✓|約定単価|
|raw_json|JSON/TEXT| |元データ|
|created_at|DATETIME|✓||

インデックス：`(order_id, fill_time)`

---

#### 4.2.8 oco_groups（自前OCOグループ）
部分約定により複数グループを持てる設計
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|batch_item_id|BIGINT FK|✓||
|qty|INT|✓|このOCOで管理する数量|
|tp_order_id|BIGINT FK| |orders.id（role=tp）|
|sl_order_id|BIGINT FK| |orders.id（role=sl）|
|status|VARCHAR(30)|✓|ACTIVE/TP_FILLED/SL_FILLED/CLOSED|
|created_at|DATETIME|✓||
|updated_at|DATETIME|✓||

インデックス：`(batch_item_id, status)`

---

#### 4.2.9 position_snapshots（建玉スナップショット：復旧・監査用）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|batch_job_id|BIGINT FK| |関連バッチ|
|captured_at|DATETIME|✓|取得時刻|
|raw_json|JSON/TEXT|✓|/positionsの結果|
|created_at|DATETIME|✓||

インデックス：`(batch_job_id, captured_at)`

---

#### 4.2.10 scheduler_runs（スケジューラ実行履歴）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|ran_at|DATETIME|✓||
|triggered_jobs|INT|✓|起動したジョブ数|
|status|VARCHAR(20)|✓|OK/ERROR|
|message|TEXT| |エラー等|
|created_at|DATETIME|✓||

---

#### 4.2.11 event_logs（イベントログ）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|batch_job_id|BIGINT FK| |関連バッチ|
|batch_item_id|BIGINT FK| |関連銘柄|
|level|VARCHAR(10)|✓|INFO/WARN/ERROR|
|event_type|VARCHAR(50)|✓|ORDER_SENT/FILL_DETECTED/OCO_CANCEL/...|
|message|TEXT|✓|人が読む要約|
|context_json|JSON/TEXT| |機械が読む詳細|
|created_at|DATETIME|✓||

インデックス：`(level, created_at)`, `(batch_job_id, created_at)`

---

#### 4.2.12 audit_logs（手動操作監査）
|列|型|必須|説明|
|---|---:|:--:|---|
|id|BIGINT PK|✓||
|user_id|BIGINT FK| |操作者|
|action|VARCHAR(50)|✓|CANCEL_ORDER/FORCE_CLOSE/PAUSE_BATCH|
|target_type|VARCHAR(20)|✓|batch_job/batch_item/order|
|target_id|BIGINT|✓|対象ID|
|reason|TEXT| |理由|
|created_at|DATETIME|✓||

---

## 5. API連携方針（内部）

### 5.1 同期（ポーリング）間隔
- `orders`：1秒〜2秒（運用で調整）
- `positions`：2秒〜5秒（信用のHoldID確定待ち時は短め）

### 5.2 例外処理
- トークン失効：再取得→再試行（ただしkabuステ側状態で失敗する可能性があるためログを残す）
- レート制限：指数バックオフ（例：0.5s, 1s, 2s…）
- 取消失敗：すでに約定/失効の可能性→状態再照会して整合

---

## 6. 実装タスク分解（次工程の開発順）

1. DBスキーマ作成（SQLiteでOK）
2. バッチ作成UI（watchlist→batch_items生成）
3. Scheduler（予約→起動）
4. Execution（エントリー一括発注）
5. Watcher（注文状態同期）
6. OCO Manager（TP/SL発注・相互取消）
7. EOD Closer（引け強制決済）
8. バッチ詳細UI（状態可視化・手動操作）
9. ログ画面 / 監査

---

## 7. 補足：最小要件での「事故りにくい」運用ガード
- RUNNING中は設定変更をロック（バッチ/銘柄の重要項目）
- 「緊急停止」ボタンを必ず用意（残OCO取消→必要なら成行）
- 画面に「最終同期時刻」を必ず表示（止まってるのが分かる）
- 当日終了時に「未決済が残っていないか」自己チェック（positionsの残を確認して警告）

---

以上
