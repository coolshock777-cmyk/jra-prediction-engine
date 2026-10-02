import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import numpy as np
import json
import os
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# ==========================================
# 0. アプリ基本設定 & セッション状態
# ==========================================
st.set_page_config(page_title="JRA AI予想 & 成績検証エンジン", page_icon="🏇", layout="wide")

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

# ==========================================
# ログイン認証処理
# ==========================================
def check_password():
    if st.session_state["authenticated"]:
        return True
    
    st.title("🔒 ログイン")
    password_input = st.text_input("パスワードを入力してください", type="password")
    if st.button("ログイン"):
        if "APP_PASSWORD" in st.secrets and password_input == st.secrets["APP_PASSWORD"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("パスワードが正しくありません。")
    return False

if not check_password():
    st.stop()

# ==========================================
# 1. 定数・距離マスター定義 (Ver.1.01 拡張)
# ==========================================
VERSION = "Ver.1.01"

TURF_DISTANCES = [
    "1000m", "1200m", "1400m", "1500m", "1600m", 
    "1800m", "2000m", "2200m", "2400m", "2500m", 
    "2600m", "3000m", "3200m", "3400m", "3600m"
]

DIRT_DISTANCES = [
    "1000m", "1150m", "1200m", "1400m", "1600m", 
    "1700m", "1800m", "2100m", "2400m", "2500m"
]

ALL_DISTANCES = sorted(list(set(TURF_DISTANCES + DIRT_DISTANCES)), key=lambda x: int(x.replace('m', '')))
ALL_DISTANCES_WITH_OTHER = ALL_DISTANCES + ["その他"]

CSV_FILENAME = "JRA_Prediction_History.csv"
CSV_COLUMNS = [
    "レースID", "レース名", "開催日", "コース", "距離", 
    "馬場状態", "勝負度", "軸馬", "相手馬", "単勝オッズ", 
    "確定フラグ", "回収額", "収支", "メモ", "投資額"
]

# ==========================================
# 2. 補助関数 (距離パース・Google Drive操作)
# ==========================================
def parse_distance_from_text(text: str) -> str:
    """出走表等の文字列からJRA主要距離マスターに存在する距離を返す。"""
    if not text:
        return "その他"
    
    match = re.search(r'(\d{4}|\d{3})\s*m', text)
    if match:
        dist_str = f"{match.group(1)}m"
        if dist_str in ALL_DISTANCES:
            return dist_str
            
    match_num = re.search(r'(\d{4}|\d{3})', text)
    if match_num:
        dist_str = f"{match_num.group(1)}m"
        if dist_str in ALL_DISTANCES:
            return dist_str
            
    return "その他"

def get_gdrive_service():
    """Secrets設定からgoogle-auth + PyDrive2経由でGoogle Driveインスタンスを取得"""
    try:
        gauth = GoogleAuth()
        gauth.credentials = None
        
        # st.secrets 取得 & 辞書化
        creds_dict = dict(st.secrets["gcp_service_account"])
        
        # private_key の改行コード多角補正（エスケープ文字列・実際の改行双方に対応）
        if "private_key" in creds_dict:
            pk = creds_dict["private_key"]
            pk = pk.replace('\\n', '\n').replace('\\\\n', '\n')
            creds_dict["private_key"] = pk
            
        # google-auth による現代的認証処理
        from google.oauth2.service_account import Credentials
        scope = ["https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        
        gauth.credentials = creds
        drive = GoogleDrive(gauth)
        return drive
    except Exception as e:
        st.error(f"Google Drive連携エラー: {e}")
        return None

def load_history_df():
    """Drive上のCSVを読み込み。存在しない場合は空のDataFrameを作成"""
    folder_id = st.secrets.get("GOOGLE_DRIVE_FOLDER_ID", "")
    drive = get_gdrive_service()
    if not drive or not folder_id:
        return pd.DataFrame(columns=CSV_COLUMNS)
    
    try:
        file_list = drive.ListFile({
            'q': f"'{folder_id}' in parents and title = '{CSV_FILENAME}' and trashed = false"
        }).GetList()
        
        if file_list:
            file_obj = file_list[0]
            file_obj.GetContentFile("temp_history.csv")
            df = pd.read_csv("temp_history.csv", encoding="utf-8-sig")
            # 不足カラムの互換性担保
            for col in CSV_COLUMNS:
                if col not in df.columns:
                    df[col] = ""
            return df[CSV_COLUMNS]
        else:
            return pd.DataFrame(columns=CSV_COLUMNS)
    except Exception as e:
        st.warning(f"履歴読み込み時の注意: {e}")
        return pd.DataFrame(columns=CSV_COLUMNS)

def save_history_df(df: pd.DataFrame):
    """Drive上のCSVへ上書き保存（確定済レコード保護を維持）"""
    folder_id = st.secrets.get("GOOGLE_DRIVE_FOLDER_ID", "")
    drive = get_gdrive_service()
    if not drive or not folder_id:
        st.error("Google Driveの保存先フォルダIDが設定されていません。")
        return False
        
    try:
        df.to_csv("temp_history.csv", index=False, encoding="utf-8-sig")
        file_list = drive.ListFile({
            'q': f"'{folder_id}' in parents and title = '{CSV_FILENAME}' and trashed = false"
        }).GetList()
        
        if file_list:
            file_obj = file_list[0]
            file_obj.SetContentFile("temp_history.csv")
            file_obj.Upload()
        else:
            file_obj = drive.CreateFile({
                'title': CSV_FILENAME,
                'parents': [{'id': folder_id}]
            })
            file_obj.SetContentFile("temp_history.csv")
            file_obj.Upload()
        return True
    except Exception as e:
        st.error(f"Google Drive保存エラー: {e}")
        return False

# ==========================================
# 3. サイドバー・画面切り替え
# ==========================================
st.sidebar.title("🏇 JRA AI予想")
st.sidebar.caption(f"現在のバージョン: **{VERSION}**")

mode = st.sidebar.radio("機能メニュー", ["🏇 リアルタイム予想", "📊 成績ダッシュボード・結果入力"])

# ==========================================
# 4. 画面 1: リアルタイム予想
# ==========================================
if mode == "🏇 リアルタイム予想":
    st.header("🏇 リアルタイム予想 & スコアリング")
    
    # 1. レースURLまたはrace_id入力
    race_url_input = st.text_input(
        "JRAレースID または レースURLを入力してください",
        placeholder="例: 202609280611 または https://netkeiba.com... race_id=202609280611"
    )
    
    if race_url_input:
        race_id_match = re.search(r'(\d{12}|\d{10})', race_url_input)
        if race_id_match:
            race_id = race_id_match.group(1)
        else:
            race_id = "202609280611" # フォールバックID
        
        st.success(f"解析対象レースID: `{race_id}`")
        
        # ダミー/Webスクレイピングデータ構築（実走時はrequests+BS4で取得）
        parsed_extracted_text = "芝1600m" # サンプル抽出結果
        default_parsed_dist = parse_distance_from_text(parsed_extracted_text)
        
        st.markdown("### ⚙️ レース条件の確認・調整")
        col_track, col_dist, col_cond = st.columns(3)
        
        with col_track:
            track_type = st.selectbox("コース種別", ["芝", "ダート", "障害"], index=0)
            
        if track_type == "芝":
            dist_options = TURF_DISTANCES + ["その他"]
        elif track_type == "ダート":
            dist_options = DIRT_DISTANCES + ["その他"]
        else:
            dist_options = ALL_DISTANCES_WITH_OTHER

        if default_parsed_dist in dist_options:
            default_index = dist_options.index(default_parsed_dist)
        else:
            default_index = dist_options.index("その他")

        with col_dist:
            selected_distance = st.selectbox(
                "距離設定 (JRA主要距離)",
                options=dist_options,
                index=default_index,
                help="自動パースされた距離です。万が一誤りがある場合は手動調整できます。"
            )

        if selected_distance == "その他":
            custom_dist = st.text_input("手動距離入力 (例: 1100m)", value="")
            final_distance = custom_dist if custom_dist else "その他"
        else:
            final_distance = selected_distance

        with col_cond:
            track_condition = st.selectbox("馬場状態", ["良", "稍重", "重", "不良"], index=0)
            
        st.info(f"📌 設定条件: **{track_type} {final_distance} ({track_condition})**")
        
        # バイアス・G1サイン等設定
        st.markdown("### 🎛️ 補正パラメータ設定")
        col_p1, col_p2, col_p3 = st.columns(3)
        with col_p1:
            is_front_runner_bias = st.checkbox("前残りバイアス (×1.08)", value=True)
            is_inside_bias = st.checkbox("内枠バイアス (×1.05)", value=False)
        with col_p2:
            is_outer_stretch_bias = st.checkbox("外差しバイアス (×1.05)", value=False)
            is_green_belt = st.checkbox("グリーンベルト (×1.06)", value=False)
        with col_p3:
            g1_mode = st.checkbox("G1サインモード加点", value=False)
            confidence_level = st.select_slider("勝負度", options=["★☆☆", "★★☆", "★★★"], value="★★☆")

        budget_amount = st.number_input("予算設定 (円)", min_value=100, value=1000, step=100)

        if st.button("🚀 AI予想を実行"):
            # デモ用出走馬データサンプル（実際のスクレイピング結果に置換）
            sample_horses = [
                {"馬番": 1, "馬名": "グランアレグリア", "騎手": "ルメール", "オッズ": 2.1},
                {"馬番": 2, "馬名": "シュネルマイスター", "騎手": "横山武", "オッズ": 4.5},
                {"馬番": 3, "馬名": "ソングライン", "騎手": "戸崎", "オッズ": 5.8},
                {"馬番": 4, "馬名": "サリオス", "騎手": "松山", "オッズ": 12.0},
                {"馬番": 5, "馬名": "ダノンザキッド", "騎手": "川田", "オッズ": 15.2},
            ]
            
            # 生スコア算定
            scores = []
            for h in sample_horses:
                # 1. 対数オッズスコア
                base_score = np.log(100.0 / h["オッズ"])
                
                # 2. 騎手補正 (ルメール/川田 ×1.15)
                jockey_mult = 1.15 if h["騎手"] in ["ルメール", "川田"] else 1.0
                
                # 3. バイアス補正
                bias_mult = 1.0
                if is_front_runner_bias and h["馬番"] <= 3:
                    bias_mult *= 1.08
                if is_inside_bias and h["馬番"] <= 2:
                    bias_mult *= 1.05
                if track_type == "ダート" and final_distance == "1200m" and h["馬番"] <= 2:
                    bias_mult *= 1.08  # ダート1200m内枠特殊補正
                if is_green_belt and h["馬番"] == 1:
                    bias_mult *= 1.06
                    
                total_score = base_score * jockey_mult * bias_mult
                scores.append(total_score)
            
            # Softmax正規化 -> モデル評価シェア(%)
            exp_scores = np.exp(scores - np.max(scores))
            model_shares = exp_scores / np.sum(exp_scores)
            
            # 評価結果の構築
            result_rows = []
            for idx, h in enumerate(sample_horses):
                share = model_shares[idx]
                val_index = share * h["オッズ"]  # AI価値指数
                result_rows.append({
                    "馬番": h["馬番"],
                    "馬名": h["馬名"],
                    "騎手": h["騎手"],
                    "単勝オッズ": h["オッズ"],
                    "評価シェア(%)": round(share * 100, 1),
                    "AI価値指数": round(val_index, 2)
                })
                
            res_df = pd.DataFrame(result_rows).sort_values(by="評価シェア(%)", ascending=False)
            
            st.markdown("### 📊 予想結果・AI評価一覧")
            st.dataframe(res_df, use_container_width=True)
            
            # 印・選出
            top_horse = res_df.iloc[0]["馬名"]
            partner_horses = ", ".join(res_df.iloc[1:3]["馬名"].tolist())
            top_odds = res_df.iloc[0]["単勝オッズ"]
            
            col_res1, col_res2 = st.columns(2)
            with col_res1:
                st.subheader(f"◎ 本命軸馬: {top_horse}")
                st.write(f"◯/▲ 相手馬: {partner_horses}")
            with col_res2:
                st.subheader("💡 資金配分イメージ")
                st.write(f"本命馬券 (50%): {int(budget_amount * 0.5)} 円")
                st.write(f"相手馬券 (30%): {int(budget_amount * 0.3)} 円")
                st.write(f"抑え馬券 (20%): {int(budget_amount * 0.2)} 円")

            # Google Driveへの保存セクション
            st.markdown("---")
            if st.button("📥 この予想結果を履歴（CSV）に保存する"):
                history_df = load_history_df()
                
                unique_race_key = f"{race_id}_{track_type}{final_distance}"
                
                # 確定済保護チェック
                existing_record = history_df[history_df["レースID"] == unique_race_key]
                if not existing_record.empty and existing_record.iloc[0]["確定フラグ"] == "確定":
                    st.warning("⚠️ このレースは既に「確定済」のため、回収額・収支は保護され上書きされません。")
                else:
                    new_record = {
                        "レースID": unique_race_key,
                        "レース名": f"レース_{race_id}",
                        "開催日": pd.Timestamp.now().strftime("%Y-%m-%d"),
                        "コース": track_type,
                        "距離": final_distance,
                        "馬場状態": track_condition,
                        "勝負度": confidence_level,
                        "軸馬": top_horse,
                        "相手馬": partner_horses,
                        "単勝オッズ": top_odds,
                        "確定フラグ": "未確定",
                        "回収額": 0,
                        "収支": -budget_amount,
                        "メモ": "",
                        "投資額": budget_amount
                    }
                    
                    # 既存の未確定レコードがあれば差分更新、なければ追加
                    if not existing_record.empty:
                        history_df = history_df[history_df["レースID"] != unique_race_key]
                    
                    updated_df = pd.concat([history_df, pd.DataFrame([new_record])], ignore_index=True)
                    
                    if save_history_df(updated_df):
                        st.success("✅ 予想履歴を Google Drive の CSV に正常保存しました！")

# ==========================================
# 5. 画面 2: 成績ダッシュボード・結果入力
# ==========================================
elif mode == "📊 成績ダッシュボード・結果入力":
