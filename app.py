import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import numpy as np

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
# 1. 定数・距離マスター定義 (Ver.1.01)
# ==========================================
VERSION = "Ver.1.01"

JRA_VENUES = ["東京", "中山", "阪神", "京都", "中京", "新潟", "福島", "小倉", "札幌", "函館"]

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

if "history_df" not in st.session_state:
    st.session_state["history_df"] = pd.DataFrame(columns=CSV_COLUMNS)

# ==========================================
# 2. 補助関数 (距離パース・データ保持)
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

def load_history_df():
    """Google Drive未接続時もセッション側でデータを保持"""
    return st.session_state["history_df"]

def save_history_df(df: pd.DataFrame):
    """履歴データの保存"""
    st.session_state["history_df"] = df
    return True

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
    
    # 1. レース基本情報設定
    st.markdown("### 📅 レース基本情報")
    col_date, col_venue, col_rnum = st.columns(3)
    
    with col_date:
        race_date = st.date_input("開催日", pd.Timestamp.now())
        
    with col_venue:
        selected_venue = st.selectbox("競馬場", JRA_VENUES, index=0)
        
    with col_rnum:
        race_num = st.selectbox("レース番号", [f"{i}R" for i in range(1, 13)], index=10)
        
    race_url_input = st.text_input(
        "JRAレースID または レースURL（任意）",
        placeholder="例: 202609280611 または netkeibaのURL"
    )
    
    race_id_match = re.search(r'(\d{12}|\d{10})', race_url_input) if race_url_input else None
    if race_id_match:
        race_id = race_id_match.group(1)
    else:
        race_id = f"{race_date.strftime('%Y%m%d')}_{selected_venue}_{race_num}"
        
    st.success(f"解析対象: **{race_date.strftime('%Y/%m/%d')} {selected_venue} {race_num}** （ID: `{race_id}`）")
    
    # 2. コース条件・距離マスター選択
    parsed_extracted_text = "芝1600m"
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
            help="自動パースされた距離です。手動での変更も可能です。"
        )

    if selected_distance == "その他":
        custom_dist = st.text_input("手動距離入力 (例: 1100m)", value="")
        final_distance = custom_dist if custom_dist else "その他"
    else:
        final_distance = selected_distance

    with col_cond:
        track_condition = st.selectbox("馬場状態", ["良", "稍重", "重", "不良"], index=0)
        
    st.info(f"📌 設定条件: **{selected_venue} {track_type} {final_distance} ({track_condition})**")
    
    # 3. 補正パラメータ設定
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

    # 4. AI予想実行
    if st.button("🚀 AI予想を実行"):
        sample_horses = [
            {"馬番": 1, "馬名": "グランアレグリア", "騎手": "ルメール", "オッズ": 2.1},
            {"馬番": 2, "馬name": "シュネルマイスター", "騎手": "横山武", "オッズ": 4.5},
            {"馬番": 3, "馬名": "ソングライン", "騎手": "戸崎", "オッズ": 5.8},
            {"馬番": 4, "馬名": "サリオス", "騎手": "松山", "オッズ": 12.0},
            {"馬番": 5, "馬名": "ダノンザキッド", "騎手": "川田", "オッズ": 15.2},
        ]
        
        scores = []
        for h in sample_horses:
            base_score = np.log(100.0 / h["オッズ"])
            jockey_mult = 1.15 if h["騎手"] in ["ルメール", "川田"] else 1.0
            bias_mult = 1.0
            if is_front_runner_bias and h["馬番"] <= 3:
                bias_mult *= 1.08
            if is_inside_bias and h["馬番"] <= 2:
                bias_mult *= 1.05
            if track_type == "ダート" and final_distance == "1200m" and h["馬番"] <= 2:
                bias_mult *= 1.08
            if is_green_belt and h["馬番"] == 1:
                bias_mult *= 1.06
                
            total_score = base_score * jockey_mult * bias_mult
            scores.append(total_score)
        
        exp_scores = np.exp(scores - np.max(scores))
        model_shares = exp_scores / np.sum(exp_scores)
        
        result_rows = []
        for idx, h in enumerate(sample_horses):
            share = model_shares[idx]
            val_index = share * h["オッズ"]
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

        st.markdown("---")
        if st.button("📥 この予想結果を履歴に保存する"):
            race_name_str = f"{selected_venue}{race_num}"
            history_df = load_history_df()
            
            existing_record = history_df[history_df["レースID"] == race_id]
            if not existing_record.empty and existing_record.iloc[0]["確定フラグ"] == "確定":
                st.warning("⚠️ このレースは既に「確定済」のため保護されています。")
            else:
                new_record = {
                    "レースID": race_id,
                    "レース名": race_name_str,
                    "開催日": race_date.strftime("%Y-%m-%d"),
                    "コース": f"{selected_venue}{track_type}",
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
                if not existing_record.empty:
                    history_df = history_df[history_df["レースID"] != race_id]
                
                updated_df = pd.concat([history_df, pd.DataFrame([new_record])], ignore_index=True)
                save_history_df(updated_df)
                st.success(f"✅ {race_name_str} の予想結果を保存しました！")

# ==========================================
# 5. 画面 2: 成績ダッシュボード・結果入力
# ==========================================
elif mode == "📊 成績ダッシュボード・結果入力":
    st.header("📊 成績ダッシュボード & 確定回収率集計")
    
    df = load_history_df()
    
    confirmed_df = df[df["確定フラグ"] == "確定"] if not df.empty else pd.DataFrame()
    
    total_races = len(df)
    confirmed_races = len(confirmed_df)
    total_investment = confirmed_df["投資額"].astype(float).sum() if not confirmed_df.empty else 0
    total_return = confirmed_df["回収額"].astype(float).sum() if not confirmed_df.empty else 0
    total_balance = total_return - total_investment
    recovery_rate = (total_return / total_investment * 100) if total_investment > 0 else 0.0
    
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("総予想件数", f"{total_races} 件")
    col_m2.metric("確定レース数", f"{confirmed_races} 件")
    col_m3.metric("通算回収率", f"{recovery_rate:.1f} %")
    col_m4.metric("累計収支", f"{int(total_balance):,} 円")
    
    st.markdown("---")
    st.subheader("📝 未確定レースの払戻金入力・更新")
    
    unconfirmed_df = df[df["確定フラグ"] == "未確定"] if not df.empty else pd.DataFrame()
    
    if unconfirmed_df.empty:
        st.info("現在、未確定のレースはありません。")
    else:
        with st.form("update_result_form"):
            selected_race_id = st.selectbox(
                "結果を入力するレースを選択",
                options=unconfirmed_df["レースID"].tolist()
            )
            
            race_detail = unconfirmed_df[unconfirmed_df["レースID"] == selected_race_id].iloc[0]
            st.caption(f"対象: **{race_detail['開催日']} {race_detail['レース名']}** | 軸馬: **{race_detail['軸馬']}** | 投資額: **{race_detail['投資額']}円**")
            
            input_return = st.number_input("回収額 / 払戻金 (円)", min_value=0, value=0, step=100)
            input_memo = st.text_input("メモ (例: 単勝的中など)", value="")
            
            submit_update = st.form_submit_button("確定成績を保存")
            
            if submit_update:
                idx = df[df["レースID"] == selected_race_id].index
                if not idx.empty:
                    inv = float(df.loc[idx[0], "投資額"])
                    df.loc[idx[0], "確定フラグ"] = "確定"
                    df.loc[idx[0], "回収額"] = input_return
                    df.loc[idx[0], "収支"] = input_return - inv
                    df.loc[idx[0], "メモ"] = input_memo
                    
                    save_history_df(df)
                    st.success(f"✅ レース `{selected_race_id}` の確定成績を更新しました！")
                    st.rerun()

    st.markdown("---")
    st.subheader("📋 全履歴ログ")
    st.dataframe(df, use_container_width=True)
    
    st.download_button(
        label="📥 最新ログ（CSV）をPCにダウンロード",
        data=df.to_csv(index=False, encoding="utf-8-sig"),
        file_name=CSV_FILENAME,
        mime="text/csv"
    )
