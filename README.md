JRA Prediction Engine Ver.2.49

今回の変更
- 結果確定時に予測履歴と関連する買い目履歴を相互反映。回収額・収支・メモ・確定状態の不整合を防止。
- 結果ページと開催日からレース終了状態を判定し、終了済みレースのモデル予想を自動で無効化。
- 同日レースは結果掲載までは予想可能状態を維持。
- Google Cloudのサービスアカウント鍵を使わず、Google Apps Script Web App経由でGoogle DriveへCSV同期。
- 買い目履歴と予測履歴のローカル保存は維持。
- 予想実行時に予測履歴を保存し、成績ダッシュボードへログを残す仕様を維持。
- 同じレースでも別の予測ログなら別買い目として保存する既存仕様を維持。
- CSVはUTF-8 BOM付きで保存し、Excelで日本語が文字化けしにくい形式を維持。
- 長いIDはアプリ内部では生文字列として保持。

追加改良（レース状態・結果表示/反映）
- 開催日が未来のレースは、結果ページの共通文言に左右されず「予想可能」と判定するよう修正。
- 同日レースの終了判定は #All_Result_Table の実着順（3件以上）を根拠に限定。
- 終了レースに「結果・着順・払戻を取得」ボタンを追加。
- 取得した着順・払戻を画面で確認できるようにし、予測履歴へ結果情報を反映できるようにした。
- 結果情報の反映では「確定フラグ」「回収額」「収支」は自動変更せず、成績確定処理と分離。
- 既存CSVに「結果着順」「結果払戻」「結果取得日時」を不足カラムとして自動追加する後方互換仕様。

初回設定
1. google_drive_webapp.gs をGoogle Apps Scriptへコピー。
2. FOLDER_ID を保存先Google DriveフォルダIDに変更。
3. WEBAPP_TOKEN を任意の長い秘密文字列に変更。
4. ウェブアプリとしてデプロイ。
   - 実行ユーザー: 自分
   - アクセスできるユーザー: 全員
5. 発行された /exec URL と同じWEBAPP_TOKENをStreamlit Secretsへ設定。

Streamlit Secrets:
APP_PASSWORD = "現在のパスワード"
GOOGLE_DRIVE_WEBAPP_URL = "発行された/exec URL"
GOOGLE_DRIVE_WEBAPP_TOKEN = "Apps Scriptと同じトークン"

重要
- サービスアカウントJSON、private_key、Google Cloudの鍵は不要。
- google_drive_webapp.gs のURLやトークンは公開しない。
- Apps Scriptを「実行ユーザー: 自分」でデプロイすることで、保存先DriveはそのGoogleアカウントの権限で操作される。
- アクセス設定を「全員」にするため、認証はWEBAPP_TOKENで行う。トークンは十分長くランダムなものを使用する。
