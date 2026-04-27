import streamlit as st
from supabase import create_client
import pandas as pd
import time

# --- НАЛАШТУВАННЯ СТОРІНКИ ---
st.set_page_config(
    page_title="SEO Commander UA",
    page_icon="🚀",
    layout="wide"
)

# --- ПІДКЛЮЧЕННЯ ДО SUPABASE ---
try:
    supabase_url = st.secrets["supabase"]["url"]
    supabase_key = st.secrets["supabase"]["key"]
    supabase = create_client(supabase_url, supabase_key)
except Exception as e:
    st.error("❌ Помилка підключення до бази даних. Перевірте secrets.toml")
    st.stop()

# --- ФУНКЦІЯ ПАГІНАЦІЇ ДЛЯ ОБХОДУ ЛІМІТУ 1000 СТРОК ---
def fetch_all_data(table_name, select_query="*", eq_column=None, eq_value=None, order_by=None):
    """Витягує всі дані з таблиці, обходячи обмеження API."""
    all_data = []
    limit = 1000
    offset = 0
    
    while True:
        query = supabase.table(table_name).select(select_query)
        
        if eq_column and eq_value is not None:
            query = query.eq(eq_column, eq_value)
            
        if order_by:
            query = query.order(order_by)
            
        # Запитуємо діапазон (в Supabase range включний: 0-999, 1000-1999 тощо)
        response = query.range(offset, offset + limit - 1).execute()
        data = response.data
        
        if not data:
            break
            
        all_data.extend(data)
        
        # Якщо повернулося менше ліміту, значить це остання "сторінка"
        if len(data) < limit:
            break
            
        offset += limit
        
    return all_data

# --- ФУНКЦІЯ АВТОМАТИЧНОЇ СИНХРОНІЗАЦІЇ КЛЮЧІВ ---
def sync_keys():
    state = st.session_state["editor"]
    sid = st.session_state["selected_site_id"]
    df_orig = st.session_state["current_df"]

    # 1. Видалення з бази
    if state["deleted_rows"]:
        ids_to_del = df_orig.iloc[state["deleted_rows"]]["id"].tolist()
        if ids_to_del:
            supabase.table("keywords").delete().in_("id", ids_to_del).execute()

    # 2. Редагування існуючих
    if state["edited_rows"]:
        for idx, changes in state["edited_rows"].items():
            row_id = df_orig.iloc[int(idx)]["id"]
            
            changes.pop("id", None)
            changes.pop("created_at", None)
            changes.pop("site_id", None)
            
            if changes:
                supabase.table("keywords").update(changes).eq("id", row_id).execute()

    # 3. Додавання нових (З ПЕРЕВІРКОЮ НА УНІКАЛЬНІСТЬ)
    if state["added_rows"]:
        # Отримуємо існуючі ключі через пагінацію
        existing_data = fetch_all_data("keywords", select_query="keyword", eq_column="site_id", eq_value=sid)
        existing_keys = set([x["keyword"].strip().lower() for x in existing_data])

        for row in state["added_rows"]:
            kw = row.get("keyword", "")
            if kw:
                kw_clean = kw.strip()
                # Перевіряємо, чи немає вже такого ключа
                if kw_clean.lower() not in existing_keys:
                    row.pop("id", None)
                    row.pop("created_at", None)
                    
                    row["site_id"] = sid
                    row["keyword"] = kw_clean
                    if "status" not in row or not row["status"]: 
                        row["status"] = "new"
                    
                    supabase.table("keywords").insert(row).execute()
                    existing_keys.add(kw_clean.lower())

st.title("🎛️ Панель Управління SEO Фермою (UA)")

tab1, tab2, tab3 = st.tabs(["➕ Додати Проект", "📊 Дашборд", "⚙️ Керування Ключами та URL"])

# ==========================================
# ВКЛАДКА 1: ДОДАВАННЯ ПРОЕКТУ
# ==========================================
with tab1:
    st.header("Налаштування нового сайту")
    with st.form("main_add_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            site_link = st.text_input("Посилання (URL)", placeholder="https://vash-sait.com.ua")
            login = st.text_input("Логін (WP Admin)")
            password = st.text_input("Звичайний пароль (для нотаток)", type="password")
            app_password = st.text_input("App Password (для API)", type="password")
        
        with col2:
            lang = st.text_input("Мова сайту", placeholder="Наприклад: ua, en, es, de")
            interval = st.number_input("Інтервал (годин)", min_value=1, value=12)
            keywords_text = st.text_area("Список тем (ключі, кожна з нового рядка)", height=150)

        if st.form_submit_button("🚀 Зберегти проект"):
            if site_link and login and app_password and lang:
                clean_link = site_link.strip().rstrip("/")
                try:
                    res = supabase.table("sites").insert({
                        "site_link": clean_link, "login": login, "password": password,
                        "app_password": app_password, "lang": lang.strip().lower(),
                        "posting_interval": interval, "is_active": True
                    }).execute()
                    
                    if res.data and keywords_text.strip():
                        sid = res.data[0]['id']
                        lines = [l.strip() for l in keywords_text.split('\n') if l.strip()]
                        unique_lines = list(set(lines)) 
                        
                        payload = [{"site_id": sid, "keyword": k, "status": "new"} for k in unique_lines]
                        # Вставка пачкою (якщо ключів більше 1000, можна теж розбити на чанки, але зазвичай з форми стільки не кидають)
                        supabase.table("keywords").insert(payload).execute()
                        
                    st.success("✅ Проект успішно додано!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e: 
                    st.error(f"Помилка: {e}")
            else:
                st.warning("Будь ласка, заповніть всі обов'язкові поля (URL, Логін, App Password, Мова).")

# ==========================================
# ВКЛАДКА 2: ДАШБОРД
# ==========================================
with tab2:
    st.header("📊 Стан публікацій")
    try:
        sites_data = supabase.table("sites").select("*").execute().data
        # Використовуємо пагінацію для всіх ключів
        keys_data = fetch_all_data("keywords", select_query="site_id, keyword, status, article_link, created_at")
        
        if sites_data:
            df_keys_all = pd.DataFrame(keys_data) if keys_data else pd.DataFrame()
            
            for s in sites_data:
                with st.expander(f"🌐 {s['site_link']} — {s['lang'].upper()}", expanded=True):
                    sk = df_keys_all[df_keys_all['site_id'] == s['id']] if not df_keys_all.empty else pd.DataFrame()
                    
                    c1, c2, c3 = st.columns(3)
                    total = len(sk)
                    done = len(sk[sk['status'].isin(['published', 'done'])]) if not sk.empty else 0
                    c1.metric("Усього тем", total)
                    c2.metric("Готово", done)
                    c3.metric("В черзі", total - done)
                    
                    if total > 0: 
                        st.progress(done / total)

                    if not sk.empty:
                        st.subheader("🔗 Останні опубліковані посилання")
                        done_links = sk[sk['status'].isin(['published', 'done'])].sort_values(by="created_at", ascending=False)
                        if not done_links.empty:
                            st.dataframe(
                                done_links[["keyword", "article_link", "created_at"]],
                                column_config={"article_link": st.column_config.LinkColumn("Посилання на пост")},
                                use_container_width=True, hide_index=True
                            )
                        else: 
                            st.info("Публікацій ще немає.")
        else: 
            st.info("Додайте сайт у вкладці 'Додати Проект'.")
    except Exception as e: 
        st.error(f"Помилка: {e}")

# ==========================================
# ВКЛАДКА 3: КЕРУВАННЯ (З ІМПОРТОМ EXCEL)
# ==========================================
with tab3:
    st.header("⚙️ Редагування бази та імпорт")
    all_sites = supabase.table("sites").select("id, site_link").execute().data
    if all_sites:
        site_map = {s['site_link']: s['id'] for s in all_sites}
        sel_name = st.selectbox("Виберіть сайт", options=list(site_map.keys()))
        sid = site_map[sel_name]
        st.session_state["selected_site_id"] = sid
        
        # --- БЛОК ІМПОРТУ З EXCEL ---
        st.divider()
        st.subheader("📥 Масовий імпорт ключів (Excel)")
        st.caption("Завантажте файл .xlsx. Ключі мають бути у першій колонці. Дублікати будуть проігноровані.")
        
        uploaded_file = st.file_uploader("Виберіть файл", type=["xlsx"])
        if uploaded_file is not None:
            if st.button("Завантажити ключі в базу"):
                try:
                    df_excel = pd.read_excel(uploaded_file, header=None)
                    new_keys = df_excel.iloc[:, 0].dropna().astype(str).str.strip().tolist()
                    new_keys = [k for k in new_keys if k]
                    
                    if new_keys:
                        # Запитуємо існуючі ключі через пагінацію
                        existing_data = fetch_all_data("keywords", select_query="keyword", eq_column="site_id", eq_value=sid)
                        existing_keys = set([x["keyword"].lower() for x in existing_data])
                        
                        keys_to_add = list(set([k for k in new_keys if k.lower() not in existing_keys]))
                        
                        if keys_to_add:
                            # Якщо ключів дуже багато, краще розбити їх на батчі (шматки по 1000) для вставки
                            batch_size = 1000
                            for i in range(0, len(keys_to_add), batch_size):
                                batch = keys_to_add[i:i + batch_size]
                                payload = [{"site_id": sid, "keyword": k, "status": "new"} for k in batch]
                                supabase.table("keywords").insert(payload).execute()

                            st.success(f"✅ Успішно додано {len(keys_to_add)} нових унікальних ключів!")
                            time.sleep(1.5)
                            st.rerun()
                        else:
                            st.info("ℹ️ Всі ключі з цього файлу вже існують у проекті.")
                    else:
                        st.warning("Файл порожній або має неправильний формат.")
                except Exception as e:
                    st.error(f"Помилка при обробці файлу: {e}")

        # --- БЛОК РЕДАГУВАННЯ ТА ВИДАЛЕННЯ ---
        st.divider()
        col_ed1, col_ed2 = st.columns([3, 1])
        with col_ed1:
            st.subheader("🔑 Керування ключами та посиланнями")
            st.caption("Змінюйте текст, статус або посилання прямо в таблиці — збережеться автоматично.")
        with col_ed2:
            with st.expander("🗑️ Видалення проекту"):
                if st.button(f"Видалити {sel_name}", type="primary"):
                    supabase.table("keywords").delete().eq("site_id", sid).execute()
                    supabase.table("sites").delete().eq("id", sid).execute()
                    st.rerun()
        
        # Витягуємо всі ключі для таблиці через пагінацію
        db_keys = fetch_all_data("keywords", select_query="*", eq_column="site_id", eq_value=sid, order_by="id")
        
        if db_keys:
            df_keys = pd.DataFrame(db_keys)
            st.session_state["current_df"] = df_keys
            st.data_editor(
                df_keys,
                column_config={
                    "id": None, "site_id": None, "created_at": None,
                    "keyword": st.column_config.TextColumn("Ключове слово", width="large"),
                    "status": st.column_config.SelectboxColumn("Статус", options=["new", "published", "error"]),
                    "article_link": st.column_config.TextColumn("URL статті (можна редагувати)")
                },
                num_rows="dynamic", use_container_width=True, key="editor", on_change=sync_keys
            )
        else:
            st.info("У цього сайту ще немає ключів. Завантажте їх через Excel або додайте вручну в таблиці.")
    else: 
        st.warning("Додайте сайт.")
