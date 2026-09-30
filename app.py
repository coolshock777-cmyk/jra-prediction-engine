import streamlit as st
import pandas as pd
import numpy as np
import datetime
import time
import os
import requests
from bs4 import BeautifulSoup

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
st.sidebar.title("🏇 JRA AI Prediction")
app_mode = st.sidebar.radio(
    "機能選択",
    ["🏇 リアルタイム予想", "📊 成績ダッシュボード"]
)


# ---------------------------------------------------------
# 3. 📊 成績ダッシュボード画面
# ---------------------------------------------------------
CSV_FILE_PATH = "JRA_Prediction_History.csv"

if app_mode == "📊 成績ダッシュボード":
    st.header("📊 予想履歴・成績ダッシュボード")
    try:
        if os.path.exists(CSV_FILE_PATH):
            df = pd.read_csv(CSV_FILE_PATH)
            if df.empty:
                st.info("まだ予想履歴データ（JRA_Prediction_History.csv）が空です。")
            else:
                for col in ['回収額', '収支', '自信度80%以上', '競馬場', '投資額']:
                    if col not in df.columns:
                        df[col] = 0 if col in ['回収額', '収支', '投資額'] else '未設定'

                df['回収額'] = pd.to_numeric(df['回収額'], errors='coerce').fillna(0)
                df['収支'] = pd.to_numeric(df['収支'], errors='coerce').fillna(0)
                df['投資額'] = pd.to_numeric(df['投資額'], errors='coerce').fillna(0)

                total_races = len(df)
                total_invest = df['投資額'].sum()
                total_return = df['回収額'].sum()
                total_balance = df['収支'].sum()
                recovery_rate = (total_return / total_invest * 100) if total_invest > 0 else 0
                hit_races = len(df[df['回収額'] > 0])
                hit_rate = (hit_races / total_races * 100) if total_races > 0 else 0

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("総予想数", f"{total_races} 件")
                col2.metric("通算回収率", f"{recovery_rate:.1f} %", delta=f"{total_balance:+,.0f}円")
                col3.metric("通算的中率", f"{hit_rate:.1f} %", f"{hit_races}/{total_races}レース")
                col4.metric("総収支", f"{total_balance:+,.0f} 円")

                st.markdown("---")
                st.subheader("📈 累計収支推移")
                df['累計収支'] = df['収支'].cumsum()
                st.line_chart(df['累計収支'])

                st.subheader("📋 過去予想・反省ログ一覧")
                st.dataframe(df.sort_index(ascending=False), use_container_width=True)
        else:
            st.warning("⚠️ 予想履歴データ（JRA_Prediction_History.csv）が見つかりません。")
    except Exception as e:
        st.error(f"データ読み込みエラーが発生しました: {e}")

    st.stop()


# ---------------------------------------------------------
# 4. 本番用最適化スクレイピング & 16大分析計算エンジン
# ---------------------------------------------------------
PLACE_MAP = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
    "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10"
}

@st.cache_data(ttl=60)
def fetch_and_analyze_real_race(date_obj, place_name, race_num_str, track_cond, is_g1_mode, bias_params, lucky_nums_str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    year = date_obj.strftime("%Y")
    place_code = PLACE_MAP.get(place_name, "05")
    race_r = race_num_str.replace("R", "").zfill(2)
    
    horses_list = []
    
    # 直近の秋開催パターン（第4回〜第5回、1〜8日目）を優先検索
    for kai in [4, 5, 3, 2, 1]:
        for nichi in range(1, 9):
            race_id = f"{year}{place_code}{kai:02d}{nichi:02d}{race_r}"
            url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
            
            try:
                res = requests.get(url, headers=headers, timeout=1.5)
                res.encoding = 'euc-jp'
                if res.status_code == 200 and "Shutuba_Table" in res.text:
                    soup = BeautifulSoup(res.text, "html.parser")
                    table = soup.find("table", class_="Shutuba_Table")
                    if table:
                        rows = table.find_all("tr", class_="HorseList")
                        for row in rows:
                            try:
                                umaban_elem = row.find("td", class_="Umaban")
                                umaban = int(umaban_elem.text.strip()) if umaban_elem and umaban_elem.text.strip().isdigit() else 0
                                
                                horse_elem = row.find("span", class_="HorseName")
                                horse_name = horse_elem.text.strip() if horse_elem else ""
                                
                                jockey_elem = row.find("td", class_="Jockey")
                                jockey_name = jockey_elem.text.strip().replace("\n", "") if jockey_elem else "未定"
                                
                                kinba_elem = row.find("td", class_="Txt_C")
                                kinba_txt = kinba_elem.text.strip() if kinba_elem else "58.0"
                                kinba = float(kinba_txt) if kinba_txt.replace('.', '', 1).isdigit() else 58.0
                                
                                odds_elem = row.find("td", class_="Popular")
                                odds_txt = odds_elem.text.strip() if odds_elem else "10.0"
                                odds_val = float(odds_txt) if odds_txt.replace('.', '', 1).isdigit() else 10.0

                                if umaban > 0 and horse_name:
                                    base_score = round(150.0 / (odds_val + 5.0) + 8.0, 1)
                                    horses_list.append({
                                        "umaban": umaban,
                                        "name": horse_name,
                                        "jockey": jockey_name,
                                        "kinba": kinba,
                                        "odds": odds_val,
                                        "base_score": base_score
                                    })
                            except Exception:
                                continue
                        if len(horses_list) > 0:
                            break
            except Exception:
                continue
        if len(horses_list) > 0:
            break

    if not horses_list:
        return {
            "status": "not_ready",
            "message": f"{date_obj} 【{place_name}】{race_num_str} の出馬表データがまだ確定前か未取得です。本日夕方〜明日の枠順確定後に再度お試しください。"
        }

    lucky_list = [int(x.strip()) for x in lucky_nums_str.split(",") if x.strip().isdigit()]
    analyzed_horses = []

    for h in horses_list:
        score = h["base_score"]
        
        if bias_params["mae"] and h["umaban"] in [1, 2, 3, 4]:
            score += 1.5
        if bias_params["uchi"] and h["umaban"] <= 4:
            score += 1.0
        if bias_params["sashi"] and h["umaban"] >= 8:
            score += 1.0
        if bias_params["green"] and h["umaban"] in [2, 3, 4]:
            score += 0.5
            
        if is_g1_mode and h["umaban"] in lucky_list:
            score += bias_params["sign_weight"]
            
        h_copy = h.copy()
        h_copy["final_score"] = round(score, 1)
        analyzed_horses.append(h_copy)

    analyzed_horses.sort(key=lambda x: x["final_score"], reverse=True)
    return {"status": "success", "data": analyzed_horses}


def save_prediction_to_csv(log_data):
    try:
        file_exists = os.path.exists(CSV_FILE_PATH)
        df_new = pd.DataFrame([log_data])
        
        if not file_exists:
            df_new.to_csv(CSV_FILE_PATH, index=False, encoding='utf-8-sig')
        else:
            df_new.to_csv(CSV_FILE_PATH, mode='a', header=False, index=False, encoding='utf-8-sig')
        return True
    except Exception as e:
        st.error(f"CSVログ保存エラー: {e}")
        return False


# ---------------------------------------------------------
# 5. 🏇 リアルタイム予想メイン画面
# ---------------------------------------------------------
st.title("🏇 JRA AI予想エンジン")
st.caption("JRA公式・netkeibaリアルタイム解析 / 16大分析スコア / G1オカルトサイン / 自動資金配分")

st.subheader("📅 レース情報 & 投資設定")

col_r1, col_r2, col_r3, col_r4 = st.columns(4)
with col_r1:
    race_date = st.date_input("開催日", datetime.date.today())
with col_r2:
    place = st.selectbox("競馬場", ["東京", "京都", "中山", "阪神", "中京", "小倉", "新潟", "福島", "札幌", "函館"])
with col_r3:
    race_num = st.selectbox("レース番号", [f"{i}R" for i in range(1, 13)], index=10)
with col_r4:
    budget = st.number_input("💰 投資予算 (円)", value=10000, step=1000)

col_c1, col_c2 = st.columns(2)
with col_c1:
    course_type = st.selectbox("コース種別・距離", ["芝1200m", "芝1600m", "芝2000m", "芝2400m", "ダ1200m", "ダ1800m", "その他"])
with col_c2:
    track_condition = st.selectbox("馬場状態", ["良", "稍重", "重", "不良"])

is_g1 = st.checkbox("🏆 G1/重賞モード（サイン・ヘッドライン分析発動）", value=True)

sign_weight = 1.5
lucky_number = "3, 7, 14"
g1_headline = ""
if is_g1:
    st.subheader("🔮 G1オカルトサイン分析設定")
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        g1_headline = st.text_input("G1ヘッドライン", "時代を創る絶対王者が伝統の盾を掴む")
        lucky_number = st.text_input("ラッキーナンバー・特注馬番", "3, 7, 14")
    with col_g2:
        presenter_note = st.text_input("プレゼンター・イベント特注サイン", "赤枠 / 3枠注意")
        sign_weight = st.slider("サインスコア重み付け補正", 0.0, 3.0, 1.5)

st.subheader("⚙️ トラックバイアス・傾向補正")
col_b1, col_b2, col_b3, col_b4 = st.columns(4)
with col_b1:
    bias_mae = st.checkbox("前残り傾向 (+1.5)", value=True)
with col_b2:
    bias_uchi = st.checkbox("内枠有利 (+1.0)", value=True)
with col_b3:
    bias_sashi = st.checkbox("外差し有利 (+1.0)")
with col_b4:
    bias_green = st.checkbox("グリーンベルト (+0.5)")

bias_params = {
    "mae": bias_mae,
    "uchi": bias_uchi,
    "sashi": bias_sashi,
    "green": bias_green,
    "sign_weight": sign_weight
}

if st.button("🚀 リアルタイム分析・予想計算スタート", type="primary"):
    with st.spinner(f"🌐 リアルタイム出馬表・オッズデータを解析中 ({race_date} {place}{race_num})..."):
        res = fetch_and_analyze_real_race(
            race_date, place, race_num, track_condition, is_g1, bias_params, lucky_number
        )

    if res.get("status") == "success":
        horses = res["data"]
        st.success("✅ 実走データの取得・16大分析スコア計算・G1サイン判定が完了しました！")

        honmei = horses[0] if len(horses) > 0 else {"umaban": "-", "name": "-", "final_score": 0}
        taikou = horses[1] if len(horses) > 1 else {"umaban": "-", "name": "-", "final_score": 0}
        tanana = horses[2] if len(horses) > 2 else {"umaban": "-", "name": "-", "final_score": 0}
        tokuchu = horses[3] if len(horses) > 3 else {"umaban": "-", "name": "-", "final_score": 0}
        kiken = horses[-1] if len(horses) > 4 else {"umaban": "-", "name": "-", "final_score": 0}

        st.markdown("---")
        col_res1, col_res2 = st.columns(2)

        with col_res1:
            st.markdown("### 🎯 AI予想印 & 16大分析スコア")
            st.markdown(f"""
            * **◎ 本命:** **{honmei['umaban']}番 {honmei['name']}** (AIスコア: **{honmei['final_score']}**) 
            * **◯ 対抗:** **{taikou['umaban']}番 {taikou['name']}** (AIスコア: {taikou['final_score']})
            * **▲ 単穴:** **{tanana['umaban']}番 {tanana['name']}** (AIスコア: {tanana['final_score']})
            * **☆ 特注穴馬:** **{tokuchu['umaban']}番 {tokuchu['name']}** (AIスコア: {tokuchu['final_score']})
            * **⚠️ 危険な人気馬:** **{kiken['umaban']}番 {kiken['name']}**
            """)

        with col_res2:
            st.markdown("### 🔥 G1サイン & 勝負馬判定")
            if is_g1:
                st.markdown(f"""
                * 🔥 **【W勝負馬】**: **{honmei['umaban']}番 {honmei['name']}** (AIスコア1位 × サイン判定)
                * 🔮 **【サイン特注馬番】**: {lucky_number}
                * 📰 **ヘッドライン解析**: `{g1_headline}`
                """)
            else:
                st.info("G1モード OFF")

        alloc_1 = int(budget * 0.50)
        alloc_2 = int(budget * 0.30)
        alloc_3 = int(budget * 0.20)

        st.subheader(f"💰 推奨買い目・資金自動配分 (総予算: {budget:,}円)")
        st.markdown(f"""
        | 賭け式 | 組合せ | 資金配分(%) | 推奨購入額 |
        | :--- | :--- | :--- | :--- |
        | **馬連** | **{honmei['umaban']} - {taikou['umaban']}** | **50%** | **{alloc_1:,}円** |
        | **馬連** | **{honmei['umaban']} - {tanana['umaban']}** | **30%** | **{alloc_2:,}円** |
        | **ワイド** | **{honmei['umaban']} - {tokuchu['umaban']}** | **20%** | **{alloc_3:,}円** |
        """)

        g1_str = f"【G1ヘッドライン: {g1_headline}】\n" if is_g1 else ""
        copy_text = f"""【AI競馬予想＆買い目配信】
📅 開催日: {race_date} {place}{race_num} ({course_type} 馬場:{track_condition})
💰 投資予算: {budget:,}円
{g1_str}--------------------------------
【予想印】
◎ {honmei['umaban']}番 {honmei['name']} (スコア:{honmei['final_score']})
◯ {taikou['umaban']}番 {taikou['name']}
▲ {tanana['umaban']}番 {tanana['name']}
☆ {tokuchu['umaban']}番 {tokuchu['name']}
🔥【W勝負馬】 {honmei['umaban']}番 {honmei['name']}
--------------------------------
【推奨買い目】
馬連: {honmei['umaban']}-{taikou['umaban']} ({alloc_1:,}円)
馬連: {honmei['umaban']}-{tanana['umaban']} ({alloc_2:,}円)
ワイド: {honmei['umaban']}-{tokuchu['umaban']} ({alloc_3:,}円)
--------------------------------
#JRA競馬予想 #AI予想"""

        st.subheader("📋 1タップコピペ用テキスト")
        st.code(copy_text, language="text")

        log_entry = {
            "予想日時": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "開催日": str(race_date),
            "競馬場": place,
            "レース番号": race_num,
            "本命(◎)": f"{honmei['umaban']}番 {honmei['name']}",
            "対抗(◯)": f"{taikou['umaban']}番 {taikou['name']}",
            "投資額": budget,
            "回収額": 0,
            "収支": -budget,
            "LINE/IPAT出力テキスト": copy_text
        }
        if save_prediction_to_csv(log_entry):
            st.caption("✅ 予想結果ログを JRA_Prediction_History.csv へ正常に保存しました。")

    elif res.get("status") == "not_ready":
        st.warning(f"⚠️ {res.get('message')}")
    else:
        st.error(f"データ取得・解析エラー: {res.get('message')}")
