# kabuステーションAPI：信用返済注文（利確・損切）パラメータルールまとめ（売・買 両方向）

> 対象：kabuステーションAPI `POST /sendorder`（RequestSendOrder）  
> 目的：**信用返済注文（CashMargin=3）**を「利確（指値）」と「損切（逆指値）」で出すときの**パラメータ指定ルール**を、  
> **買建（ロング）返済**／**売建（ショート）返済**の両方向で整理します。  
>
> 注：ここでの「方向」は **建玉の種類（買建/売建）**に対して、返済の注文が **売（Side=1）か買（Side=2）か** が変わる、という意味です。

---

## 1. 返済注文の前提（共通）

### 1-1. 返済注文として成立させる必須条件（信用返済）
- `CashMargin`：`"3"`（返済）
- `MarginTradeType`：信用区分（制度/一般長期/一般デイトレ等）を指定（返済でも必須）
- `DelivType`：**信用返済では 0（指定なし）不可**。信用返済に合う受渡方法を指定
- 返済対象の指定は **次のどちらか一方**（同時指定は不可）
  - **返済順**：`ClosePositionOrder`（一括返済の順序）
  - **建玉指定**：`ClosePositions`（建玉IDと数量を配列で指定）

### 1-2. 建玉ID（HoldID）のルール
- `ClosePositions[].HoldID` は **`E` から始まる建玉ID**（例：`E2026...`）
- 返済は「どの建玉を返すか」が重要。部分返済なら `ClosePositions[].Qty` で数量を分割指定する

---

## 2. Side（売買区分）の最重要ルール（買建／売建で逆になる）

| 建玉（保有ポジション） | 返済で出す注文の Side | 意味 |
|---|---:|---|
| **信用買い建て（ロング）** | `Side="1"`（売） | 売って返す（利確も損切も“売”） |
| **信用売り建て（ショート）** | `Side="2"`（買） | 買い戻して返す（利確も損切も“買”） |

> つまり、利確/損切の別ではなく、**建玉がロングかショートか**で Side が決まります。

---

## 3. 利確（指値返済）のルール（共通）

利確は基本的に **返済の指値**として出します。

- `FrontOrderType`：`20`（指値）
- `Price`：指値価格を指定（成行ではないため 0 にしない）
- `ExpireDay`：`0`（当日）または `yyyyMMdd`

### 3-1. ロング（買建）利確：売（Side=1）
- ロングの利確は「高く売って返済」＝ **売の指値返済**

### 3-2. ショート（売建）利確：買（Side=2）
- ショートの利確は「安く買い戻して返済」＝ **買の指値返済**

---

## 4. 損切（逆指値返済）のルール（共通）

損切は基本的に **返済の逆指値**として出します。

- `FrontOrderType`：`30`（逆指値）
- `ReverseLimitOrder`：**必須**
- `Price`：0（逆指値では通常 0 を入れる運用が多い／逆指値条件は ReverseLimitOrder 側）

### 4-1. ReverseLimitOrder（逆指値条件）の必須項目
| 項目 | 役割 | 代表値 |
|---|---|---|
| `TriggerSec` | トリガ参照先 | `1`=発注銘柄 / `2`=NK225 / `3`=TOPIX |
| `TriggerPrice` | トリガ価格 | 例：`1950` |
| `UnderOver` | 「以下」「以上」 | `1`=以下 / `2`=以上 |
| `AfterHitOrderType` | ヒット後の執行条件 | `1`=成行 / `2`=指値 / `3`=不成 |
| `AfterHitPrice` | ヒット後価格 | 成行なら `0`、指値/不成なら価格 |

---

## 5. UnderOver（以上/以下）の考え方：ロング損切とショート損切

損切は「不利な方向に動いたら発動」なので、**ロングとショートで UnderOver の使い方が逆**になりやすいです。

| 建玉 | 損切の発動条件（一般例） | 逆指値設定（例） |
|---|---|---|
| **ロング（買建）** | 価格が下がったら損切 | `UnderOver=1`（以下）, `TriggerPrice=損切ライン` |
| **ショート（売建）** | 価格が上がったら損切 | `UnderOver=2`（以上）, `TriggerPrice=損切ライン` |

> 例外：あなたの戦略や参照指標（NK225/TOPIX）で変わるので、上表は「一般的な株価連動の損切」を想定しています。

---

## 6. 最小テンプレ（共通フィールド）

返済注文テンプレ（最小構成の目安）：

```json
{
  "Symbol": "9433",
  "Exchange": 1,
  "SecurityType": 1,

  "Side": "1 or 2",
  "CashMargin": "3",
  "MarginTradeType": 1,
  "DelivType": 2,
  "AccountType": 4,

  "Qty": 100,

  "ClosePositions": [
    { "HoldID": "E2026xxxxxxxx", "Qty": 100 }
  ],

  "FrontOrderType": 20,
  "Price": 0,
  "ExpireDay": 0
}
```

---

## 7. 例：利確（指値返済）— ロング/ショート

### 7-1. ロング（買建）利確：**売（Side=1）指値返済**
```json
{
  "Symbol": "9433",
  "Exchange": 1,
  "SecurityType": 1,

  "Side": "1",
  "CashMargin": "3",
  "MarginTradeType": 1,
  "DelivType": 2,
  "AccountType": 4,

  "Qty": 100,
  "ClosePositions": [
    { "HoldID": "E2026xxxxxxxx", "Qty": 100 }
  ],

  "FrontOrderType": 20,
  "Price": 2100,
  "ExpireDay": 0
}
```

### 7-2. ショート（売建）利確：**買（Side=2）指値返済**
```json
{
  "Symbol": "9433",
  "Exchange": 1,
  "SecurityType": 1,

  "Side": "2",
  "CashMargin": "3",
  "MarginTradeType": 1,
  "DelivType": 2,
  "AccountType": 4,

  "Qty": 100,
  "ClosePositions": [
    { "HoldID": "E2026xxxxxxxx", "Qty": 100 }
  ],

  "FrontOrderType": 20,
  "Price": 1800,
  "ExpireDay": 0
}
```

---

## 8. 例：損切（逆指値返済）— ロング/ショート

### 8-1. ロング（買建）損切：**売（Side=1）逆指値返済**（価格が下がったら発動）
```json
{
  "Symbol": "9433",
  "Exchange": 1,
  "SecurityType": 1,

  "Side": "1",
  "CashMargin": "3",
  "MarginTradeType": 1,
  "DelivType": 2,
  "AccountType": 4,

  "Qty": 100,
  "ClosePositions": [
    { "HoldID": "E2026xxxxxxxx", "Qty": 100 }
  ],

  "FrontOrderType": 30,
  "Price": 0,
  "ExpireDay": 0,

  "ReverseLimitOrder": {
    "TriggerSec": 1,
    "TriggerPrice": 1950,
    "UnderOver": 1,
    "AfterHitOrderType": 1,
    "AfterHitPrice": 0
  }
}
```

### 8-2. ショート（売建）損切：**買（Side=2）逆指値返済**（価格が上がったら発動）
```json
{
  "Symbol": "9433",
  "Exchange": 1,
  "SecurityType": 1,

  "Side": "2",
  "CashMargin": "3",
  "MarginTradeType": 1,
  "DelivType": 2,
  "AccountType": 4,

  "Qty": 100,
  "ClosePositions": [
    { "HoldID": "E2026xxxxxxxx", "Qty": 100 }
  ],

  "FrontOrderType": 30,
  "Price": 0,
  "ExpireDay": 0,

  "ReverseLimitOrder": {
    "TriggerSec": 1,
    "TriggerPrice": 2050,
    "UnderOver": 2,
    "AfterHitOrderType": 1,
    "AfterHitPrice": 0
  }
}
```

---

## 9. よくあるミス（チェックリスト）

- [ ] `CashMargin="3"` にしている（返済）
- [ ] ロング/ショートに応じて `Side` を正しくしている（ロング返済=売、ショート返済=買）
- [ ] `ClosePositionOrder` と `ClosePositions` を **同時に入れていない**
- [ ] `ClosePositions[].HoldID` が **E 始まり**の建玉IDになっている
- [ ] 損切（逆指値）では `FrontOrderType=30` かつ `ReverseLimitOrder` を入れている
- [ ] 逆指値の `UnderOver` がロング/ショートで意図どおりになっている
- [ ] `DelivType` を 0 にしていない（信用返済では不可）

---

## 10. 追記：利確・損切を「同時に持つ」運用について

kabuステーションAPIの注文は「1回の /sendorder で 1注文」です。  
利確（指値返済）と損切（逆指値返済）を同時に置きたい場合は、**2回発注**が基本になります。

ただし、片方が約定したらもう片方を取り消すなどの管理が必要です（OCO相当の運用を自前で実装）。

---

### 更新メモ
- このMDは「公式仕様（kabu_STATION_API.yaml）」のルールを前提に、実運用で間違えやすい **Side/UnderOver** を両方向で整理したものです。
