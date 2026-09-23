# 公式フロントエンドの呼出判定

2026-09-21、さわやかとラ・オハナ 横浜本牧の公開HTMLとJavaScriptを確認。
認証情報を使用せず、受付申込・取消は実行していません。

確認した公式スクリプト:

- https://exclusive-mini.junbanmachi.jp/sawayaka/_scripts/bc76ba5.modern.js
- https://exclusive-mini.junbanmachi.jp/la-ohana-yokohamahonmoku/_scripts/144eb38.modern.js

両方の定義は WAITING=2、CALLING=4、PENDING=5、ENTER=6、
PRE_CALLING=8、USING=9、LOST=10、USED=11 です。
`isCalled` は CALLING のみで真となります。PRE_CALLING は事前呼出メッセージ、
CALLING は呼出メッセージ、PENDING は保留メッセージを表示します。
ENTER では完了ページへ遷移します。通常の詳細ポーリング間隔は20秒です。
`call_count` と `is_coming` は確認ダイアログの条件に使われます。

本サービスも数値 status=4 の観測だけを呼出確認として扱います。
残り0組は通知対象ですが、呼出確認や自動タスク完了の根拠にはしません。
`called_at` は初回確認の観測時刻であり、サーバー側の正確な呼出時刻でも
入店時刻でもありません。欠測後に保留・完了を見ても呼出時刻を補完しません。

この一致は確認した2加盟店の配信バージョンに対する証拠です。
他の加盟店・将来のバージョンとの完全互換性は保証しません。
