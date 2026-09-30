import streamlit as st
import pandas as pd
import numpy as np

# ---------------------------------------------------------
# 1. 簡易パスワード認証機能
# ---------------------------------------------------------
def check_password():
    if "APP_PASSWORD" not in st.secrets:
        st.error("Secrets に 'APP_PASSWORD' が設定されていません。")
        return False

    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if st.session_state["password_correct"]:
        return True

    st.title("🔒 ログイン")
    password_input = st.text_input("パスワードを入力してください", type="password")

    if st.button("ログイン"):
        if password_input == st.secrets["APP_PASSWORD"]:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("❌ パスワードが正しくありません")

    return False

if not check_password():
    st.stop()


# ---------------------------------------------------------
# 2. メニュー選択（サイドバー）
# ---------------------------------------------------------
app_mode = st.sidebar.radio(
    "機能選択",
    ["🏇 リアルタイム予想", "📊 成績ダッシュボード"]
)


# ---------------------------------------------------------
# 3. 成績ダッシュボードが選ばれた場合
# ---------------------------------------------------------
if app_mode == "📊 成績ダッシュボード":
    st.header("📊 予想履歴・成績ダッシュボード")
    try:
        df = pd.read_csv("JRA_Prediction_History.csv")
        if df.empty:
            st.info("まだ予想履歴データが登録されていません。")
        else:
            for col in ['投資額', '回収額', '自信度', '競馬場']:
                if col not in df.columns:
                    df[col] = 0 if col in ['投資額', '回収額'] else '未設定'

            df['投資額'] = pd.to_numeric(df['投資額'], errors='coerce').fillna(0)
            df['回収額'] = pd.to_numeric(df['回収額'], errors='coerce').fillna(0)
            df['収支'] = df['回収額'] - df['投資額']

            total_races = len(df)
            total_invest = df['投資額'].sum()
            total_return = df['回収額'].sum()
            total_balance = total_return - total_invest
            recovery_rate = (total_return / total_invest * 100) if total_invest > 0 else 0
            hit_races = len(df[df['回収額'] > 0])
            hit_rate = (hit_races / total_races * 100) if total_races > 0 else 0

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("総予想数", f"{total_races} 件")
            col2.metric("通算回収率", f"{recovery_rate:.1f} %", delta=f"{total_balance:+,.0f}円")
            col3.metric("通算的中率", f"{hit_rate:.1f} %")
            col4.metric("総収支", f"{total_balance:+,.0f} 円")

            st.markdown("---")
            st.subheader("📈 累計収支推移")
            df['累計収支'] = df['収支'].cumsum()
            st.line_chart(df['累計収支'])

            st.subheader("📋 過去ログ一覧")
            st.dataframe(df.sort_index(ascending=False), use_container_width=True)
    except FileNotFoundError:
        st.warning("⚠️ 予想履歴データ（JRA_Prediction_History.csv）が見つかりません。")
    except Exception as e:
        st.error(f"エラーが発生しました: {e}")

    # ダッシュボード表示時はここで処理を終了して予想エンジンを表示させない
    st.stop()


# ---------------------------------------------------------
# 4. 元の予想エンジン（ここから下は字下げ不要で元のコードをそのまま配置）
# ---------------------------------------------------------
# ⚠️ ここに元のコードをそのまま貼り付けてOKです（字下げ不要）
