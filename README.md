JRA Prediction Engine Ver.2.46

今回の修正は保存・Google Drive同期まわりのみ。予想・脚質・騎手・調教師ロジックは変更していません。

修正内容
- JRA_Bet_History.csv の「メモ」等の文字列列を明示的に文字列型へ統一
- pandas 2.x の LossySetitemError を防止
- 買い目結果確定前にも型を再正規化
- CSVを一時ファイル経由で安全に保存
- JRA_Prediction_History.csv と JRA_Bet_History.csv の保存処理を共通化
- Google Drive API設定がある場合は同名CSVをDriveフォルダへ更新/新規作成
- 共有フォルダ/Shared Driveでも同期できるよう supportsAllDrives に対応
- サービスアカウントJSONの文字列/辞書形式、private_key の改行表現に対応
- Drive同期結果を画面状態へ記録し、保存成功とDrive同期成功を区別
- Drive同期が失敗してもローカルCSV保存は成功扱いにしてログ消失を防止
- 同一レースでも予測ログIDが異なる再予想は別買い目として保存（レースIDだけで重複排除しない）

Google Drive同期設定（Streamlit secrets）
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON = サービスアカウントJSON全体
GOOGLE_DRIVE_FOLDER_ID = CSVを保存するGoogle DriveフォルダID
GOOGLE_DRIVE_SHARED_DRIVE_ID = （任意）Shared Driveを使う場合のDrive ID

サービスアカウントには対象フォルダへの編集権限を付与してください。Shared Driveを使う場合は、そのShared Drive側でも適切な権限を付与してください。
設定がない場合は従来どおりローカルCSVのみ保存します。
