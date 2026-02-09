# kabuステーション®API ざっくり整理（使い方 / 何ができるか / どこを見ればいいか）

> 対象: kabuステーション®API（PCで kabuステーション を起動している前提で、ローカルに対して REST / PUSH で操作する API）

---

## 0. まず結論：どこで確認する？
公式情報の「優先順位」はこの順が鉄板です。

1. **開発者ポータル（ガイド/リファレンス/サンプル/FAQ）**  
   - トップ: https://kabucom.github.io/kabusapi/ptal/ 〔ガイド・リファレンスへの入口〕 citeturn0search4  
   - REST API リファレンス（エンドポイント一覧）: https://kabucom.github.io/kabusapi/reference/index.html citeturn0search0  
2. **GitHub（仕様変更・不具合・質問の一次情報）**  
   - Issues 一覧: https://github.com/kabucom/kabusapi/issues citeturn0search5  
   - 直近の重要トピック例（2026/2/28 仕様変更関連の質問）: https://github.com/kabucom/kabusapi/issues/1103 citeturn0search2  
3. **規定・利用条件（やっていいこと/責任範囲）**  
   - kabuステーションAPI サービス利用規定（PDF）: https://kabu.com/pdf/Gmkpdf/service/kabustationapiuserpolicy.pdf citeturn0search3  
   - API 連携サービス利用規定（PDF）: https://kabu.com/pdf/Gmkpdf/service/APIServiceRules.pdf citeturn0search6  

補足：FAQにも「APIの利用方法」案内があります（公式）。  
https://faq.kabu.com/s/article/k002338 citeturn0search8

---

## 1. kabuステーションAPIの前提（超重要）
- **ローカルで動く**タイプのAPIです。  
  「どこかのサーバにつなぐ」のではなく、**自分のPCで起動中の kabuステーション に `localhost` で接続**します。 citeturn0search7  
- **任意の開発言語**からデータ取得・注文執行が可能（REST形式が中心）。 citeturn0search11  

---

## 2. 何ができる？（機能の大枠）
「何ができるか」の正確な一覧は **REST / PUSH のリファレンス**に載っています。 citeturn0search0turn0search4  

ここでは “実務で使う観点” で大枠を整理します。

### 2.1 REST API（要求→応答）
- **認証 / トークン発行**（まずここから） citeturn0search0  
- **銘柄・市場データの取得**（例：価格、板、指数など ※提供範囲はリファレンス準拠） citeturn0search0  
- **注文の発注**（現物/信用など、注文種別はリファレンス準拠） citeturn0search0  
- **注文の訂正/取消** citeturn0search0  
- **約定/注文状況/残高・建玉などの照会** citeturn0search0  

### 2.2 PUSH API（購読→随時受信）
- **リアルタイム配信（WebSocket等）**で、板/約定などを受け取る用途。  
  ※具体の配信チャンネルや形式は PUSH リファレンスに従います。 citeturn0search4  

---

## 3. 最短の使い方（初動の流れ）
> “まず動かす” の最小ルートです。細部はガイド/リファレンス優先。 citeturn0search4turn0search0  

1) **kabuステーション側で API を有効化**  
   - kabuステーションの設定画面で API を有効にし、**APIパスワード**を設定します。  
   - 「APIキー不一致」系は、設定ミス/再起動漏れ等が原因になりやすいので、再設定→再起動が定番切り分けです。 citeturn0search1  

2) **トークン発行（/token）**  
   - REST リファレンスの「認証 / トークン発行」を参照し、APIPassword を渡して token を取得。 citeturn0search0  

3) **各API呼び出し（Authorization で token を利用）**  
   - 以降、注文/照会/データ取得をリファレンスのエンドポイント通りに実行。 citeturn0search0  

---

## 4. 接続先（エンドポイント）メモ
- 本番: `http://localhost:18080/`  
- 検証: `http://localhost:18081/`  
（GitHubのQ&Aで繰り返し言及されている“よくある確認点”） citeturn0search7  

---

## 5. 自動売買を組むときの典型フロー（設計の骨格）
あなたの要件（OCO自前実装・監視・引け前強制決済 など）に合わせると、よくある骨格はこうなります。

1. **起動時**：token 取得（期限/失効に備えて再取得ロジックも用意） citeturn0search0  
2. **発注**：新規注文（複数銘柄まとめて） citeturn0search0  
3. **監視**：  
   - RESTで注文状態/約定照会をポーリングするか、  
   - PUSHで約定/板などのイベントを購読して判断（設計次第） citeturn0search4turn0search0  
4. **OCO（自前）**：利確が約定→損切を取消 / 損切が約定→利確を取消（取消APIを使う） citeturn0search0  
5. **引け前**：所定時刻（例：閉場30分前）で未決済を成行でクローズ（発注+必要なら取消） citeturn0search0  

---

## 6. 直近の注意点（仕様変更・運用）
- **2026年2月末（2/28）に向けた仕様変更の話題がGitHub上で活発**です。  
  例：市場コード「1（東証）」指定の現物・信用の新規発注ができなくなる、等に関する質問が出ています。 citeturn0search2turn0search9  
  → 自動売買は「市場コード」「銘柄指定」「発注パラメータ」に影響が出る可能性があるので、Issue/告知を追うのがおすすめです。 citeturn0search5  

---

## 7. 参考リンク（すぐ戻れるように）
- 開発者ポータル: https://kabucom.github.io/kabusapi/ptal/ citeturn0search4  
- REST API リファレンス: https://kabucom.github.io/kabusapi/reference/index.html citeturn0search0  
- GitHub Issues: https://github.com/kabucom/kabusapi/issues citeturn0search5  
- kabuステーションAPI サービス利用規定(PDF): https://kabu.com/pdf/Gmkpdf/service/kabustationapiuserpolicy.pdf citeturn0search3  
- API 連携サービス利用規定(PDF): https://kabu.com/pdf/Gmkpdf/service/APIServiceRules.pdf citeturn0search6  
- 公式FAQ（APIの利用方法）: https://faq.kabu.com/s/article/k002338 citeturn0search8  

---

## 8. 次にやると良いこと（あなたの用途向け）
- 「**注文・取消・約定照会・残高/建玉照会**」のエンドポイントを、あなたの仕様（OCO/予約実行/引け前決済）に沿って  
  **“必要APIチェックリスト”**に落とし込む  
- 2026/2/28前後の仕様変更に向けて、**市場コード指定**まわりを先に確認する citeturn0search2turn0search9  

必要なら、次のステップとして「あなたの仕様 → 必要API → 主要パラメータ → 状態遷移」の対応表（設計書の核）まで、同じMDに追記して整えます。
