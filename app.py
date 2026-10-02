import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import numpy as np
import os
from datetime import datetime

# ==========================================
# 0. アプリ基本設定 & セッション状態
# ==========================================
st.set_page_config(page_title="JRA オッズ＋バイアス分析エンジン", page_icon="🏇", layout="wide")

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

# ログイン認証処理
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
# 1. 定数・マスター定義 (Ver.1.09)
# ==========================================
VERSION = "Ver.1.09"

JRA_VENUES = ["東京", "中山", "阪神", "京都", "中京", "新潟", "福島", "小倉", "札幌", "函館"]
VENUE_CODE_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"
}

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
    "レースID", "レース名", "開催日", "予想日時", "コース", "距離", 
    "馬場状態", "出走頭数", "勝負度", "軸馬", "相手馬", "単勝オッズ", 
    "バイアス履歴", "モデルバージョン", "確定フラグ", "回収額", "収支", "メモ", "投資額"
]

def sanitize_df_types(df: pd.DataFrame) -> pd.DataFrame:
    numeric_cols = ["出走頭数", "単勝オッズ", "回収額", "収支", "投資額"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df

def load_history_df():
    if os.path.exists(CSV_FILENAME):
        try:
            df = pd.read_csv(CSV_FILENAME, encoding="utf-8-sig")
            for col in CSV_COLUMNS:
                if col not in df.columns:
                    df[col] = ""
            df = sanitize_df_types(df[CSV_COLUMNS])
            st.session_state["history_df"] = df
            return df
        except Exception:
            pass
            
    if "history_df" not in st.session_state:
        st.session_state["history_df"] = pd.DataFrame(columns=CSV_COLUMNS)
    return st.session_state["history_df"]

def save_history_df(df: pd.DataFrame):
    st.session_state["history_df"] = df
    try:
        df.to_csv(CSV_FILENAME, index=False, encoding="utf-8-sig")
    except Exception as e:
        st.error(f"ファイル保存エラー: {str(e)}")
    return True

if "latest_prediction" not in st.session_state:
    st.session_state["latest_prediction"] = None

load_history_df()

# ==========================================
# 2. 解析補助関数 & netkeibaスクレイピング
# ==========================================
def parse_race_id_metadata(race_id: str):
    if len(race_id) == 12 and race_id.isdigit():
        year = race_id[:4]
        venue_code = race_id[4:6]
        r_num_str = race_id[10:12]
        venue_name = VENUE_CODE_MAP.get(venue_code, "東京")
        try:
            r_num = int(r_num_str)
        except:
            r_num = 11
        return year, venue_name, r_num
    return None, None, 11

def parse_distance_from_text(text: str) -> str:
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

def fetch_netkeiba_race_data(race_id_or_url: str):
    """netkeibaから出走表・脚質・騎手・単勝オッズを解析"""
    race_id_match = re.search(r'(\d{12})', race_id_or_url)
    if not race_id_match:
        return None, "有効な12桁のレースIDが見つかりません。"
        
    race_id = race_id_match.group(1)
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        res.encoding = 'euc-jp'
        
        if "RaceData01" not in res.text:
            return None, "レースページの取得に失敗しました（未開催またはURL無効）。"

        soup = BeautifulSoup(res.text, 'html.parser')
        year_meta, auto_venue, auto_rnum = parse_race_id_metadata(race_id)
        
        extracted_info = {
            "race_id": race_id,
            "track_type": "芝",
            "distance": "1600m",
            "venue": auto_venue if auto_venue else "東京",
            "race_num": auto_rnum,
            "condition": "良",
            "race_name": f"レース_{race_id}",
            "race_date": None
        }
        
        race_data_02 = soup.find('div', class_='RaceData02')
        if race_data_02:
            date_match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', race_data_02.text)
            if date_match:
                y, m, d = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
                extracted_info["race_date"] = datetime(y, m, d)

        if extracted_info["race_date"] is None:
            return None, "開催日の自動取得に失敗しました。URLまたはレースIDを確認してください。"

        race_title_elem = soup.find('div', class_='RaceName')
        if race_title_elem:
            extracted_info["race_name"] = race_title_elem.text.strip()

        race_data_intro = soup.find('div', class_='RaceData01')
        if race_data_intro:
            text_info = race_data_intro.text
            if "ダ" in text_info or "ダート" in text_info:
                extracted_info["track_type"] = "ダート"
            elif "障" in text_info:
                extracted_info["track_type"] = "障害"
                
            dist_parsed = parse_distance_from_text(text_info)
            if dist_parsed != "その他":
                extracted_info["distance"] = dist_parsed
                
            if "不良" in text_info:
                extracted_info["condition"] = "不良"
            elif "稍重" in text_info or "稍" in text_info:
                extracted_info["condition"] = "稍重"
            elif "重" in text_info:
                extracted_info["condition"] = "重"
            else:
                extracted_info["condition"] = "良"

        horse_rows = soup.find_all('tr', class_='HorseList')
        if not horse_rows:
            return None, "出走表テーブルが見つかりませんでした。"
            
        horses = []
        kyaku_count = 0
        front_runner_count = 0

        for row in horse_rows:
            try:
                wakuban_elem = row.find('td', class_=re.compile('Waku'))
                wakuban = int(wakuban_elem.text.strip()) if wakuban_elem and wakuban_elem.text.strip().isdigit() else 0

                umaban_elem = row.find('td', class_=re.compile('Umaban'))
                umaban = int(umaban_elem.text.strip()) if umaban_elem and umaban_elem.text.strip().isdigit() else 0
                
                horse_elem = row.find('span', class_='HorseName')
                horse_name = horse_elem.text.strip() if horse_elem else ""
                
                jockey_elem = row.find('td', class_='Jockey')
                jockey = jockey_elem.text.strip() if jockey_elem else ""
                
                kyakushitsu = "不明"
                kyaku_elem = row.find('td', class_=re.compile('Kyakushitsu|Style|RunningStyle'))
                if not kyaku_elem:
                    kyaku_elem = row.find('span', class_=re.compile('Kyakushitsu|Style'))
                
                if kyaku_elem and kyaku_elem.text.strip():
                    kyakushitsu = kyaku_elem.text.strip()
                    kyaku_count += 1
                    if "逃" in kyakushitsu or "先" in kyakushitsu:
                        front_runner_count += 1

                odds_elem = row.find('span', class_=re.compile('Popular_Ninki|Odds'))
                odds = None
                if odds_elem:
                    match_odds = re.search(r'\b(\d+\.\d+)\b', odds_elem.text.strip())
                    if match_odds:
                        try:
                            odds = float(match_odds.group(1))
                        except ValueError:
                            odds = None
                
                if odds is None or odds <= 0:
                    return None, f"オッズが未確定（発売前）または不完全です。[対象: 馬番{umaban} {horse_name}]"
                    
                if horse_name and umaban > 0:
                    horses.append({
                        "枠番": wakuban,
                        "馬番": umaban,
                        "馬名": horse_name,
                        "騎手": jockey,
                        "脚質": kyakushitsu,
                        "オッズ": odds
                    })
            except Exception:
                continue
                
        if len(horses) == 0:
            return None, "出走馬データの抽出件数が0件です。"
            
        extracted_info["horses"] = horses
        extracted_info["kyaku_count"] = kyaku_count
        extracted_info["front_runner_count"] = front_runner_count
        return extracted_info, None
        
    except requests.exceptions.RequestException as req_err:
        return None, f"通信エラーが発生しました: {str(req_err)}"
    except Exception as e:
        return None, f"解析エラーが発生しました: {str(e)}"

# ==========================================
# 3. サイドバー・画面切り替え
# ==========================================
st.sidebar.title("🏇 JRA AI予想 engine")
st.sidebar.caption(f"現在のバージョン: **{VERSION}**")

mode = st.sidebar.radio("機能メニュー", ["🏇 リアルタイム予想", "📊 成績ダッシュボード・結果入力"])

# ==========================================
# 4. 画面 1: リアルタイム予想
# ==========================================
if mode == "🏇 リアルタイム予想":
    st.header("🏇 リアルタイム予想 & スコアリング")
    st.caption("※ 対数空間（Log-Space）によるオッズベース＋バイアス加算スコアリングモデルです。")
    
    st.markdown("### 🔗 レースURL / IDの自動取得")
    race_url_input = st.text_input(
        "netkeiba レースURL または レースID（12桁）を入力してください",
        placeholder="例: https://race.netkeiba.com/race/shutuba.html?race_id=202405020811"
    )
    
    fetched_info = None
    if race_url_input:
        with st.spinner("netkeibaから出走表・コース条件・脚質・オッズを自動解析中..."):
            info, err = fetch_netkeiba_race_data(race_url_input)
            if err:
                st.error(f"❌ {err}")
            else:
                fetched_info = info
                kyaku_rate = fetched_info['kyaku_count'] / len(fetched_info['horses'])
                st.success(
                    f"✅ 解析完了: **{fetched_info['race_name']}** "
                    f"（{len(fetched_info['horses'])}頭 / {fetched_info['venue']}{fetched_info['race_num']}R / "
                    f"{fetched_info['track_type']}{fetched_info['distance']} [{fetched_info['condition']}] / "
                    f"開催日:{fetched_info['race_date'].strftime('%Y/%m/%d')}）\n\n"
                    f"💡 データ詳細: 脚質検出 {fetched_info['kyaku_count']}/{len(fetched_info['horses'])}頭 ({kyaku_rate:.0%}) "
                    f"（逃げ・先行: {fetched_info['front_runner_count']}頭）"
                )
                if kyaku_rate < 0.8:
                    st.warning("⚠️ 脚質データの取得率が低下しています。前残りバイアスの設定にご注意ください。")

    # 1. レース基本情報設定
    st.markdown("### 📅 レース基本情報")
    col_date, col_venue, col_rnum = st.columns(3)
    
    default_venue_idx = 0
    if fetched_info and fetched_info["venue"] in JRA_VENUES:
        default_venue_idx = JRA_VENUES.index(fetched_info["venue"])

    default_rnum_idx = (fetched_info["race_num"] - 1) if (fetched_info and 1 <= fetched_info["race_num"] <= 12) else 10
    default_date_val = fetched_info["race_date"] if fetched_info else pd.Timestamp.now()

    with col_date:
        race_date = st.date_input("開催日", default_date_val)
    with col_venue:
        selected_venue = st.selectbox("競馬場", JRA_VENUES, index=default_venue_idx)
    with col_rnum:
        race_num = st.selectbox("レース番号", [f"{i}R" for i in range(1, 13)], index=default_rnum_idx)
        
    race_id = fetched_info["race_id"] if fetched_info else f"{race_date.strftime('%Y%m%d')}_{selected_venue}_{race_num}"
    race_display_name = fetched_info["race_name"] if fetched_info else f"{selected_venue}{race_num}"

    # 2. コース条件・距離マスター選択
    st.markdown("### ⚙️ レース条件の確認・調整")
    col_track, col_dist, col_cond = st.columns(3)
    
    default_track = fetched_info["track_type"] if fetched_info else "芝"
    track_idx = ["芝", "ダート", "障害"].index(default_track) if default_track in ["芝", "ダート", "障害"] else 0

    with col_track:
        track_type = st.selectbox("コース種別", ["芝", "ダート", "障害"], index=track_idx)
        
    if track_type == "芝":
        dist_options = TURF_DISTANCES + ["その他"]
    elif track_type == "ダート":
        dist_options = DIRT_DISTANCES + ["その他"]
    else:
        dist_options = ALL_DISTANCES_WITH_OTHER

    default_parsed_dist = fetched_info["distance"] if fetched_info else "1600m"
    default_index = dist_options.index(default_parsed_dist) if default_parsed_dist in dist_options else dist_options.index("その他")

    with col_dist:
        selected_distance = st.selectbox("距離設定 (JRA主要距離)", options=dist_options, index=default_index)

    if selected_distance == "その他":
        custom_dist = st.text_input("手動距離入力 (例: 1100m)", value="")
        final_distance = custom_dist if custom_dist else "その他"
    else:
        final_distance = selected_distance

    default_cond = fetched_info["condition"] if fetched_info else "良"
    cond_idx = ["良", "稍重", "重", "不良"].index(default_cond) if default_cond in ["良", "稍重", "重", "不良"] else 0

    with col_cond:
        track_condition = st.selectbox("馬場状態", ["良", "稍重", "重", "不良"], index=cond_idx)
        
    st.info(f"📌 設定条件: **{race_display_name}** ({selected_venue}{race_num} {track_type}{final_distance} [{track_condition}])")
    
    # 3. 補正パラメータ設定
    st.markdown("### 🎛️ 補正パラメータ設定")
    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        is_front_runner_bias = st.checkbox("前残りバイアス (+log1.08)", value=True)
        is_inside_bias = st.checkbox("内枠バイアス (1-2枠 +log1.05)", value=False)
    with col_p2:
        is_outer_stretch_bias = st.checkbox("外差しバイアス (7-8枠 +log1.05)", value=False)
        is_green_belt = st.checkbox("グリーンベルト (1番馬 +log1.06)", value=False)
    with col_p3:
        g1_mode = st.checkbox("G1サインモード加点 (+log1.03)", value=False)
        confidence_level = st.select_slider("勝負度", options=["★☆☆", "★★☆", "★★★"], value="★★☆")

    budget_amount = st.number_input("予算設定 (円)", min_value=100, value=1000, step=100)

    # ガード処理
    if not fetched_info:
        st.warning("👈 上記に netkeiba のレースURL または 12桁ID を入力してデータを読み込んでください。")
        run_button_disabled = True
    else:
        run_button_disabled = False

    if st.button("🚀 AI予想を実行", disabled=run_button_disabled):
        target_horses = fetched_info["horses"]
        total_horses = len(target_horses)
        scores = []
        
        # ① 対数空間 (Log-Space) での数学的加算計算モデル
        for h in target_horses:
            odds_val = float(h["オッズ"])
            score = -np.log(odds_val) # 基本対数スコア
            
            jockey_str = str(h.get("騎手", "")).replace(" ", "").replace(" ", "")
            if "ルメール" in jockey_str or "川田" in jockey_str:
                score += np.log(1.15)
                
            horse_num = int(h["馬番"])
            waku_num = int(h.get("枠番", 0))
            kyaku = str(h.get("脚質", ""))

            # 脚質・バイアス条件計算（対数空間での安全加算）
            kyaku_rate = fetched_info['kyaku_count'] / len(fetched_info['horses'])
            if is_front_runner_bias and kyaku_rate >= 0.8 and ("逃" in kyaku or "先" in kyaku):
                score += np.log(1.08)
            if is_inside_bias and (waku_num in [1, 2] if waku_num > 0 else horse_num <= 2):
                score += np.log(1.05)
            if is_outer_stretch_bias and (waku_num in [7, 8] if waku_num > 0 else horse_num >= (total_horses - 2)):
                score += np.log(1.05)
            if track_type == "ダート" and final_distance == "1200m" and (waku_num in [1, 2] if waku_num > 0 else horse_num <= 2):
                score += np.log(1.08)
            if is_green_belt and horse_num == 1:
                score += np.log(1.06)
            if g1_mode and horse_num in [1, 3, 7]:
                score += np.log(1.03)
                
            scores.append(score)
        
        # ② ソフトマックス変換による相対シェア算出
        exp_scores = np.exp(scores - np.max(scores))
        model_shares = exp_scores / np.sum(exp_scores)
        
        result_rows = []
        for idx, h in enumerate(target_horses):
            share = model_shares[idx]
            val_index = share * float(h["オッズ"])
            result_rows.append({
                "枠番": int(h.get("枠番", 0)),
                "馬番": int(h["馬番"]),
                "馬名": str(h["馬名"]),
                "騎手": str(h.get("騎手", "")),
                "脚質": str(h.get("脚質", "不明")),
                "単勝オッズ": float(h["オッズ"]),
                "モデル相対シェア(%)": round(share * 100, 1),
                "モデル評価指数": round(val_index, 2)
            })
            
        res_df = pd.DataFrame(result_rows).sort_values(by="モデル相対シェア(%)", ascending=False)
        
        top_horse = res_df.iloc[0]["馬名"]
        partner_horses = ", ".join(res_df.iloc[1:min(3, len(res_df))]["馬名"].tolist())
        top_odds = res_df.iloc[0]["単勝オッズ"]

        active_biases = []
        if is_front_runner_bias: active_biases.append("前残り")
        if is_inside_bias: active_biases.append("内枠")
        if is_outer_stretch_bias: active_biases.append("外差し")
        if is_green_belt: active_biases.append("グリーンベルト")
        if g1_mode: active_biases.append("G1サイン")
        bias_str = ",".join(active_biases) if active_biases else "なし"

        st.session_state["latest_prediction"] = {
            "race_id": race_id,
            "race_name": race_display_name,
            "race_date": race_date.strftime("%Y-%m-%d"),
            "predict_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "course": f"{selected_venue}{track_type}",
            "distance": final_distance,
            "condition": track_condition,
            "head_count": total_horses,
            "confidence": confidence_level,
            "res_df": res_df,
            "top_horse": top_horse,
            "partner_horses": partner_horses,
            "top_odds": top_odds,
            "budget": budget_amount,
            "bias_str": bias_str
        }

    latest = st.session_state["latest_prediction"]
    if latest is not None:
        st.markdown("### 📊 予想結果・モデル評価一覧")
        st.dataframe(latest["res_df"], use_container_width=True)
        
        col_res1, col_res2 = st.columns(2)
        with col_res1:
            st.subheader(f"◎ 本命軸馬（相対シェア1位）: {latest['top_horse']}")
            st.write(f"◯/▲ 相手馬（相対シェア2・3位）: {latest['partner_horses']}")
            st.caption(f"適用バイアス: {latest['bias_str']}")
        with col_res2:
            st.subheader("💡 資金配分イメージ")
            b_amt = latest["budget"]
            st.write(f"本命馬券 (50%): {int(b_amt * 0.5)} 円")
            st.write(f"相手馬券 (30%): {int(b_amt * 0.3)} 円")
            st.write(f"抑え馬券 (20%): {int(b_amt * 0.2)} 円")

        st.markdown("---")
        if st.button("📥 この予想結果を履歴（CSV）に保存する"):
            history_df = load_history_df()
            save_race_id = latest["race_id"]
            
            existing_record = history_df[history_df["レースID"] == save_race_id]
            if not existing_record.empty and existing_record.iloc[0]["確定フラグ"] == "確定":
                st.warning("⚠️ このレースは既に「確定済」のため保護されています。")
            else:
                new_record = {
                    "レースID": save_race_id,
                    "レース名": latest["race_name"],
                    "開催日": latest["race_date"],
                    "予想日時": latest["predict_time"],
                    "コース": latest["course"],
                    "距離": latest["distance"],
                    "馬場状態": latest["condition"],
                    "出走頭数": latest["head_count"],
                    "勝負度": latest["confidence"],
                    "軸馬": latest["top_horse"],
                    "相手馬": latest["partner_horses"],
                    "単勝オッズ": latest["top_odds"],
                    "バイアス履歴": latest["bias_str"],
                    "モデルバージョン": VERSION,
                    "確定フラグ": "未確定",
                    "回収額": 0,
                    "収支": 0,
                    "メモ": "",
                    "投資額": latest["budget"]
                }
                if not existing_record.empty:
                    history_df = history_df[history_df["レースID"] != save_race_id]
                
                updated_df = pd.concat([history_df, pd.DataFrame([new_record])], ignore_index=True)
                save_history_df(updated_df)
                st.success(f"✅ {latest['race_name']} の予想結果を永続保存しました！")

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
            bias_info = race_detail.get('バイアス履歴', 'なし')
            m_ver = race_detail.get('モデルバージョン', '旧Ver')
            st.caption(f"対象: **{race_detail['開催日']} {race_detail['レース名']}** | 軸馬: **{race_detail['軸馬']}** | Ver: **{m_ver}** | 投資額: **{race_detail['投資額']}円**")
            
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
                    st.success(f"✅ レース `{selected_race_id}` の確定成績を更新保存しました！")
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
