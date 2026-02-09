# kabuステーション®API まとめ（リファレンス/使い方/仕様）

> 対象：三菱UFJ eスマート証券（旧 auカブコム証券）の **kabuステーション®API**  
> 最終確認日：2026-02-09（JST）  
> この資料は「公式ポータル/公式OpenAPI(YAML)/公式告知(Issue)」を中心に要点を整理した“開発者向けメモ”です。  
> **完全なリファレンスは公式の OpenAPI 仕様（YAML）を見るのが最速**です（後述）。

---

## 0. 全体像（超ざっくり）

- kabuステーション®（Windowsアプリ）を **APIモードで起動**し、**ローカルホスト（localhost）** で提供されるREST APIにアクセスする方式  
- 認証は **APIパスワード → トークン発行（/token） → 以後 X-API-KEY で呼び出し**  
- 時価更新の購読は **WebSocket(PUSH API)** を別に使う（登録銘柄の更新が来る）
- 銘柄登録はRESTで最大 **50銘柄**（REST/PUSHの合算）  
- 流量制限（目安）がある（発注系/情報系/取引余力など）  
- 本番/検証の2系統：  
  - 本番：`http://localhost:18080/kabusapi`  
  - 検証：`http://localhost:18081/kabusapi`  
  - PUSH：`ws://localhost:18080/kabusapi/websocket` / `ws://localhost:18081/kabusapi/websocket`  

---

## 1. 公式ドキュメント（まずここ）

### 1) ポータル（入口）
- ポータル：`https://kabucom.github.io/kabusapi/ptal/`
  - REST API リファレンス（Swagger UI）
  - PUSH API リファレンス
  - サンプルコード（Python/JS/C#/Excel等）
  - ガイドライン/利用規定

### 2) OpenAPI（完全な仕様＝“正”）
- OpenAPI YAML（raw）：  
  `https://raw.githubusercontent.com/kabucom/kabusapi/master/reference/kabu_STATION_API.yaml`

このYAMLには **エンドポイント、パラメータ、スキーマ、説明、制限の注記** がまとまっています。  
自動生成（openapi-generator等）にも使えます。

### 3) 公式「お知らせ」（仕様変更はここが最速）
- 例：2026年2月下旬〜の仕様変更（市場コードの扱い）  
  `https://github.com/kabucom/kabusapi/issues/1072`
- 例：2026-02-28 から「Exchange=1（東証）」で新規発注できなくなる件に関する質問  
  `https://github.com/kabucom/kabusapi/issues/1103`

---

## 2. 利用条件・前提（重要）

- kabuステーション®APIは **kabuステーション® Professionalプラン以上の適用で無料で利用可能**（条件は公式案内を参照）  
  参考：`https://kabu.com/item/kabustation_api/default.html`
- kabuステーション®APIのIDは kabuステーション®に準拠し、**同時に利用できるkabuステーション®は1つ**（複数起動は不可）  
  参考：上記同ページ

---

## 3. 接続先（本番/検証）と共通ヘッダ

### ベースURL
OpenAPIの servers 定義より：
- 本番：`http://localhost:18080/kabusapi`
- 検証：`http://localhost:18081/kabusapi`

### 共通ヘッダ
- 認証が必要なAPIは基本的に `X-API-KEY: <token>` を付与（/token 以外）

---

## 4. 認証（トークン発行）

### 手順
1. kabuステーション®を起動し、**API利用設定（APIパスワード設定）** を済ませる  
2. kabuステーション®を **APIモードでログイン**（UI上でAPIボタン点灯/「(API)」表示など）
3. `POST /token` で APIトークンを取得
4. 以後は `X-API-KEY` にトークンを付けてREST APIを呼ぶ

### トークンの失効タイミング（OpenAPIの記載）
- kabuステーションを終了した時
- kabuステーションからログアウトした時
- 別のトークンが新たに発行された時
- ※早朝に強制ログアウトがある旨の注意書きあり

---

## 5. レート制限・上限（ざっくり目安）

OpenAPI（tagsの説明）に、目安として以下が記載されています：

- 発注系：秒間5件ほど
- 情報系：秒間10件ほど
- 取引余力：秒間10件ほど
- API登録銘柄：REST/PUSH合算で最大50銘柄  
- 同一銘柄への同時注文：同時に5件ほどを上限の目安（sendorderの説明）

> 実運用では、エラー（429等）を前提に、リトライ間隔・キュー制御・同時実行数制限を組み込むのが安全です。

---

## 6. REST API（主要エンドポイント一覧）

OpenAPI YAML（v1.5）に基づく “よく使う” ものをピックアップしています。  
（フル一覧は Swagger UI または YAML を参照）

### 認証（auth）
- `POST /token`：トークン発行

### 発注（order）
- `POST /sendorder`：注文発注（現物・信用）
- `POST /sendorder/future`：注文発注（先物）
- `POST /sendorder/option`：注文発注（オプション）
- `PUT /cancelorder`：注文取消

### 取引余力（wallet）
- `GET /wallet/cash`：取引余力（現物）
- `GET /wallet/cash/{symbol}`：取引余力（現物・銘柄指定）
- （他にも信用/先物/OP等の余力系が定義されています）

### 情報（info）
- `GET /board/{symbol}`：時価情報・板情報
- `GET /orders`：注文約定照会（注文一覧）
- （他にも残高照会、銘柄情報、ランキング、規制情報などが多く定義されています）
- `GET /symbolname/future`：先物銘柄コード取得（クエリで FutureCode を指定）

### 銘柄登録（register）
PUSH（WebSocket）で配信する銘柄の管理をするRESTです。
- `PUT /register`：銘柄登録
- `PUT /unregister`：銘柄登録解除
- `PUT /unregister/all`：銘柄登録を全解除

> 銘柄登録は「API登録銘柄リスト」に反映され、登録できる数は最大50銘柄（REST/PUSH合算）。

---

## 7. PUSH API（WebSocket）

PUSH API リファレンスより：

- エンドポイント：
  - 本番：`ws://localhost:18080/kabusapi/websocket`
  - 検証：`ws://localhost:18081/kabusapi/websocket`
- 配信されるのは、RESTの「銘柄登録」で登録した銘柄（最大50）  
- 値が更新されるタイミングで配信（場間/引け後の配信は無し、という注意書きあり）
- レスポンスの各フィールドのコード値は、RESTの「時価情報・板情報」参照

参考：`https://kabucom.github.io/kabusapi/ptal/push.html`

---

## 8. 仕様変更（2026-02-28付近の注意）

2025-12-02 の公式告知（Issue）では、**2026年2月下旬（予定）** から
「現物/信用の新規発注で、市場コード Exchange=1（東証）を廃止し、東証への新規注文は Exchange=9（SOR）または 27（東証＋）を指定する」
とされています。

- 告知：`https://github.com/kabucom/kabusapi/issues/1072`
- その後のユーザ質問として、**2026-02-28から**同変更が適用される旨に触れたIssue：  
  `https://github.com/kabucom/kabusapi/issues/1103`

> 実装上は「Exchange=1」を固定している箇所（特に /sendorder の現物・信用）を点検し、  
> 新規発注は 9/27 の指定に切り替える必要が出る可能性があります。  
> 返済注文の例外（既存建玉の返済など）も告知に注意書きがあるため、必ず一次情報を確認してください。

---

## 9. 実装の型（サンプル）

### 9.1 curl（トークン → 板取得）
```bash
# 1) token
curl -s -X POST "http://localhost:18080/kabusapi/token"   -H "Content-Type: application/json"   -d '{"APIPassword":"YOUR_API_PASSWORD"}'

# 2) board
curl -s "http://localhost:18080/kabusapi/board/9432"   -H "X-API-KEY: YOUR_TOKEN"
```

### 9.2 Python（requests）
```python
import requests

BASE = "http://localhost:18080/kabusapi"
API_PASSWORD = "YOUR_API_PASSWORD"

# token
r = requests.post(f"{BASE}/token", json={"APIPassword": API_PASSWORD})
r.raise_for_status()
token = r.json()["Token"]

# board
r = requests.get(f"{BASE}/board/9432", headers={"X-API-KEY": token})
r.raise_for_status()
print(r.json())
```

### 9.3 WebSocket（PUSHを受ける）
- 事前に `PUT /register` で銘柄を登録してから接続します
- 受け取れるフィールドはPUSHリファレンスを参照

---

## 10. “詰まりどころ”チェックリスト（よくある）

- kabuステーションが **APIモードで起動できていない**（ログイン時のAPIボタン/表示、ポートが開かない等）
- セキュリティ対策・ログイン仕様変更で、以前と画面/導線が変わっている（アップデートで挙動が変わることがある）
- トークン失効（kabuステーション再起動・ログアウト・早朝強制ログアウト・別トークン発行）
- レート制限（429）や同時注文上限にかかる
- 登録銘柄上限（50）により、PUSH/RESTの利用が突然失敗する

---

## 11. 開発に便利な使い方（OpenAPI活用）

- Swagger UI（公式）：RESTの入出力を目視で確認できる
- OpenAPI Generator：YAMLからクライアントSDKを生成できる  
  例：`openapi-generator-cli generate -i <yaml> -g typescript-axios -o out --skip-validate-spec`
  - `--skip-validate-spec` が必要になるケースがある（仕様内の文字などでvalidatorが落ちることがあるため）

---

## 付録：この資料で参照した一次情報

- ポータル（入口）：https://kabucom.github.io/kabusapi/ptal/
- OpenAPI YAML：https://raw.githubusercontent.com/kabucom/kabusapi/master/reference/kabu_STATION_API.yaml
- PUSH API：https://kabucom.github.io/kabusapi/ptal/push.html
- 公式サービス案内：https://kabu.com/item/kabustation_api/default.html
- 仕様変更告知：https://github.com/kabucom/kabusapi/issues/1072
- 仕様変更に関する質問（2026-02-28言及）：https://github.com/kabucom/kabusapi/issues/1103
