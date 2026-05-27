import streamlit as st
import pandas as pd
import pydeck as pdk
from pyproj import Transformer
import os
import glob
import geopandas as gpd  # 用於處理 SHP 檔案
import base64            # 用於將照片轉為網頁 Base64 編碼以達成全螢幕與滾輪放大功能
import streamlit.components.v1 as components
import hashlib           # 用於將特徵雜湊為固定隨機顏色
import folium
from streamlit_folium import st_folium

# --- 1. 網頁基本設定 ---
st.set_page_config(layout="wide", page_title="五股已完工設施管理系統")

# --- 2. 座標轉換器 (TWD97 -> WGS84) ---
transformer = Transformer.from_crs("epsg:3826", "epsg:4326", always_xy=True)

@st.cache_data
def load_data(file_path):
    xls = pd.ExcelFile(file_path)
    df = pd.read_excel(xls, '總表')
    df.columns = [str(c).strip().replace('\n', '') for c in df.columns]
    
    df_logic = df.copy()
    for col in ['集水區', '取水設施']:
        if col in df_logic.columns:
            df_logic[col] = df_logic[col].replace(r'^\s*$', pd.NA, regex=True).ffill()

    def process_row(i):
        if pd.notna(df.loc[i, '分水鞍']) and str(df.loc[i, '分水鞍']).strip() != "":
            exact_id = str(df.loc[i, '分水鞍'])
            category_zh = f"分水鞍({exact_id})"
            color = [255, 0, 0, 200]
        elif pd.notna(df.loc[i, '分水箱']) and str(df.loc[i, '分水箱']).strip() != "":
            exact_id = str(df.loc[i, '分水箱'])
            category_zh = f"分水箱({exact_id})"
            color = [0, 0, 0, 255]
        else:
            exact_id = str(df_logic.loc[i, '取水設施'])
            category_zh = f"取水設施({exact_id})"
            color = [0, 0, 255, 200]

        ele_val = df.loc[i, '高程'] if '高程' in df.columns else None
        if pd.notna(ele_val) and str(ele_val).strip() != "":
            elevation_zh = f"{ele_val}m"
        else:
            elevation_zh = "無資訊"

        try:
            x_col = [c for c in df.columns if 'X' in c.upper()][0]
            y_col = [c for c in df.columns if 'Y' in c.upper()][0]
            lon, lat = transformer.transform(df.loc[i, x_col], df.loc[i, y_col])
        except:
            lat, lon = None, None
            
        return pd.Series([exact_id, lat, lon, color, category_zh, elevation_zh])

    df[['_filter_id', 'lat', 'lon', 'fill_color', '_tooltip_category', '_tooltip_elevation']] = df.apply(lambda row: process_row(row.name), axis=1)
    df['_filter_basin'] = df_logic['集水區']
    df['_filter_w_series'] = df_logic['取水設施']
    
    # 將「總管數」轉為純整數字串物件，徹底去掉小數點
    if '總管數' in df.columns:
        df['總管數'] = pd.to_numeric(df['總管數'], errors='coerce')
        df['總管數'] = df['總管數'].apply(lambda x: str(int(x)) if pd.notna(x) else None)
        
    return df

@st.cache_data
def load_shp_layer(shp_path, is_basin=False):
    if os.path.exists(shp_path):
        try:
            gdf = gpd.read_file(shp_path)
            gdf_wgs84 = gdf.to_crs(epsg=4326)
            geojson_obj = gdf_wgs84.__geo_interface__
            
            if is_basin:
                for idx, feature in enumerate(geojson_obj['features']):
                    geom_str = str(feature['geometry'].get('coordinates', idx))
                    hash_val = int(hashlib.md5(geom_str.encode('utf-8')).hexdigest(), 16)
                    
                    r = (hash_val & 0xFF0000) >> 16
                    g = (hash_val & 0x00FF00) >> 8
                    b = (hash_val & 0x0000FF)
                    
                    rgb_color = [int(r) % 100 + 5, g % 100 + 60, b % 100 + 80, 120]
                    
                    if 'properties' not in feature or feature['properties'] is None:
                        feature['properties'] = {}
                    feature['properties']['fill_color'] = rgb_color
            
            return geojson_obj
        except Exception as e:
            st.sidebar.error(f"SHP 讀取錯誤 ({os.path.basename(shp_path)}): {e}")
            return None
    return None

# --- 3. 讀取路徑設定 ---
FILE_PATH = "109到114年度 五股已完工設施盤點.xlsx"
PHOTO_BASE_DIR = os.path.join(".", "現勘照片/2026_4~5月/觀音山系")
SHP_BASIN_PATH = "SHP檔/觀音山系/觀音山系集水區分區.shp"
SHP_PIPE_PATH = "SHP檔/觀音山系/觀音山系管路.shp"

try:
    df_all = load_data(FILE_PATH)
except Exception as e:
    st.error(f"❌ 讀取失敗: {e}")
    st.stop()

geojson_basin = load_shp_layer(SHP_BASIN_PATH, is_basin=True)
geojson_pipe = load_shp_layer(SHP_PIPE_PATH, is_basin=False)

# 初始化 跨組件選擇狀態 的 Session State 儲存器
if "last_selected_index" not in st.session_state:
    st.session_state["last_selected_index"] = None
if "previous_clicked_popup" not in st.session_state:
    st.session_state["previous_clicked_popup"] = None

# --- 4. 側邊欄：雙層連動選單 ---
st.sidebar.title("🔍 設施篩選")

st.sidebar.markdown(
    '<span style="font-size: 18px; font-weight: bold; color: #1;">🌊 1. 選擇集水區</span>', 
    unsafe_allow_html=True
)
basin_raw_list = sorted([str(b) for b in df_all['_filter_basin'].unique() if pd.notna(b)])
basin_options = ["所有集水區"] + basin_raw_list
selected_basin = st.sidebar.selectbox("", basin_options, label_visibility="collapsed")

if "current_basin_track" not in st.session_state or st.session_state["current_basin_track"] != selected_basin:
    st.session_state["current_basin_track"] = selected_basin
    st.session_state["last_selected_index"] = None

if selected_basin == "all" or selected_basin == "所有集水區":
    df_step1 = df_all
else:
    df_step1 = df_all[df_all['_filter_basin'] == selected_basin]

st.sidebar.markdown("\n") 

st.sidebar.markdown(
    '<span style="font-size: 18px; font-weight: bold; color: #1;">📍 2. 選擇取水設施 (W系列)</span>', 
    unsafe_allow_html=True
)
w_raw_list = sorted([str(w) for w in df_step1['_filter_w_series'].unique() if 'W' in str(w).upper()])
w_options = ["所有取水設施"] + w_raw_list
selected_w = st.sidebar.selectbox("", w_options, label_visibility="collapsed")

if selected_w == "all" or selected_w == "所有取水設施":
    df_filtered = df_step1.copy().reset_index(drop=True)
else:
    df_filtered = df_step1[df_step1['_filter_w_series'] == selected_w].copy().reset_index(drop=True)

# --- 5. 主畫面：空間地圖呈現 ---
st.title(f"🗺️ 五股已完工設施管理系統")
st.subheader(f"當前範圍：{selected_basin} > {selected_w}")

# 建立控制列：讓底圖樣式與開關並排呈現
control_col1, control_col2 = st.columns([5, 5])
with control_col1:
    map_style_choice = st.radio("選擇底圖樣式：", ["電子通用地圖", "正射影像圖"], horizontal=True)
with control_col2:
    show_basin_shp = st.checkbox("顯示集水區分區範圍", value=True, help="取消勾選即可隱藏地圖上的集水區顏色圖塊")
    st.markdown(
        '<div style="color: #999; font-size: 0.9rem; margin-top: -5px; font-weight: 500;">'
        '💡 提示：點選地圖上設施，下方圖文及相關資訊會對應跳出。'
        '</div>', 
        unsafe_allow_html=True
    )

if not df_filtered.empty and not df_filtered['lat'].isnull().all():
    valid_map = df_filtered.dropna(subset=['lat', 'lon'])
    center_lat = valid_map['lat'].mean()
    center_lon = valid_map['lon'].mean()
    
    lat_span = valid_map['lat'].max() - valid_map['lat'].min()
    lon_span = valid_map['lon'].max() - valid_map['lon'].min()
    max_span = max(lat_span, lon_span)
    
    if max_span > 0.02:       zoom_level = 13
    elif max_span > 0.008:     zoom_level = 14
    elif max_span > 0.003:     zoom_level = 15
    elif max_span > 0.0008:    zoom_level = 16
    else:                      zoom_level = 17

    m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom_level, tiles=None)

    # 隱藏 Popup 樣式
    popup_css = """
    <style>
    .leaflet-popup {
        display: none !important;
        opacity: 0 !important;
        visibility: hidden !important;
        height: 0px !important;
        width: 0px !important;
    }
    .leaflet-popup-content-wrapper, .leaflet-popup-tip-container {
        display: none !important;
        height: 0px !important;
        width: 0px !important;
    }
    </style>
    """
    m.get_root().header.add_child(folium.Element(popup_css))

    if map_style_choice == "正射影像圖":
        folium.TileLayer(
            tiles="https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}",
            attr="內政部國土測繪中心正射影像", name="正射影像圖", overlay=False, control=True
        ).add_to(m)
    else:
        folium.TileLayer(
            tiles="https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}",
            attr="內政部國土測繪中心臺灣通用電子地圖", name="臺灣通用電子地圖", overlay=False, control=True
        ).add_to(m)

    if geojson_basin and show_basin_shp:
        folium.GeoJson(
            geojson_basin, name="集水區分區範圍",
            style_function=lambda feature: {
                'fillColor': f"rgba({feature['properties']['fill_color'][0]}, {feature['properties']['fill_color'][1]}, {feature['properties']['fill_color'][2]}, 0.5)",
                'color': '#333333', 'weight': 2, 'fillOpacity': 0.5
            }
        ).add_to(m)

    if geojson_pipe:
        folium.GeoJson(
            geojson_pipe, name="已完工管線",
            style_function=lambda x: {'color': '#4CAF50', 'weight': 4, 'opacity': 0.8}
        ).add_to(m)

    for idx, row in valid_map.iterrows():
        r, g, b = row['fill_color'][0], row['fill_color'][1], row['fill_color'][2]
        color_hex = f'#{r:02x}{g:02x}{b:02x}'
        
        tooltip_html = f"""
        <div style="font-family: 'Microsoft JhengHei', sans-serif; font-size: 14px; padding: 5px; line-height: 1.6; font-weight: 700;">
            <b>集水區 :</b> {row['_filter_basin']}<br/>
            <b>設施類別 :</b> {row['_tooltip_category']}<br/>
            <b>高程 :</b> {row['_tooltip_elevation']}
        </div>
        """
        
        folium.CircleMarker(
            location=[row['lat'], row['lon']],
            radius=5.5,
            tooltip=tooltip_html,
            popup=folium.Popup(str(row['_filter_id']), parse_html=True), 
            color='#FFFFFF', weight=1.5, fill=True, fill_color=color_hex, fill_opacity=0.9
        ).add_to(m)

    map_key = f"folium_map_{selected_basin}_{selected_w}_{map_style_choice}_{show_basin_shp}"
    map_data = st_folium(m, use_container_width=True, height=500, key=map_key)
    
    # 攔截地圖點擊資料
    if map_data and map_data.get("last_object_clicked_popup"):
        clicked_popup_id = map_data["last_object_clicked_popup"].strip()
        
        if clicked_popup_id != st.session_state["previous_clicked_popup"]:
            st.session_state["previous_clicked_popup"] = clicked_popup_id
            matched_indices = df_filtered[df_filtered['_filter_id'] == clicked_popup_id].index.tolist()
            if matched_indices:
                st.session_state["last_selected_index"] = matched_indices[0]
                st.rerun()

    pipe_legend_html = '&nbsp;|&nbsp; <span style="color:#4CAF50; font-weight:bold;">━</span> 已完工管線' if geojson_pipe else ''
    st.markdown(
        f'<p style="font-size: 16px; font-weight: bold; color: #1; margin-top: 10px; letter-spacing: 1px;">'
        f'🔵 取水設施 (W) &nbsp;|&nbsp; ⚫ 分水箱 (B) &nbsp;|&nbsp; 🔴 分水鞍{pipe_legend_html}'
        f'</p>', unsafe_allow_html=True
    )
else:
    st.warning("⚠️ 目前選擇的範圍內查無座標數據。")

st.divider()

base_cols = ['集水區', '取水設施', '分水箱', '分水鞍']
map_cols = {
    "基本資訊": [c for c in df_filtered.columns if '97_x' in c or '97_y' in c or '高程' in c],
    "設施說明": [c for c in df_filtered.columns if '口徑' in c or '總管長' in c or '總管數' in c or '接管數' in c or '接管率' in c],
    "其餘資訊": [c for c in df_filtered.columns if '旱灌補助' in c or '造造' in c or '推動年' in c]
}

col_filter_choice = st.radio("📋 請選擇欲檢視的表格欄位分類：", ["基本資訊", "設施說明", "其餘資訊"], horizontal=True)

final_display_cols = base_cols + map_cols[col_filter_choice]
table_df = df_filtered[df_filtered.columns.intersection(final_display_cols)].copy()

for col in table_df.columns:
    if '接管率' in col:
        def format_percentage(val):
            if pd.isna(val) or val == "" or str(val).strip() == "": return ""
            try:
                num = float(str(val).replace('%', '').strip())
                if num <= 1.0 and num > 0: num = num * 100
                return f"{int(num)}%"
            except: return str(val)
        table_df[col] = table_df[col].apply(format_percentage)

table_df = table_df.fillna("")

st.markdown("""
    <style>
        .stDataFrame div[data-testid="stTable"] td, 
        .stDataFrame div[data-testid="data-grid-canvas"] { font-size: 1.15rem !important; }
        div[data-testid="stDataFrame"] { font-size: 1.1rem !important; }
    </style>
""", unsafe_allow_html=True)

# ----------------- 5:5 佈局 -----------------
main_col_left, main_col_right = st.columns([5, 5])

# === 【左側主欄位】 ===
with main_col_left:
    st.subheader("設施清單")
    
    selected_rows = st.dataframe(
        table_df, 
        use_container_width=True, 
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row"
    )

    active_rows = []
    if selected_rows and "selection" in selected_rows:
        active_rows = selected_rows["selection"].get("rows", [])

    if len(active_rows) > 0:
        st.session_state["last_selected_index"] = active_rows[0]

# === 【右側主欄位】 ===
with main_col_right:
    st.subheader("📸 設施現勘照片")
    if st.session_state["last_selected_index"] is not None:
        idx = st.session_state["last_selected_index"]
        if idx >= len(df_filtered):
            st.session_state["last_selected_index"] = None
            st.rerun()
            
        target_row = df_filtered.iloc[idx]
        facility_id = str(target_row['_filter_id'])
        facility_basin = str(target_row['_filter_basin'])
        
        st.markdown(f"""
            <div style="background-color: #e1f5fe; padding: 12px; border-radius: 5px; border-left: 6px solid #0288d1; margin-bottom: 20px;">
                <h3 style="margin: 0; color: #01579b; font-size: 1.35rem;">📍 當前檢視設施：{facility_id} ({facility_basin})</h3>
            </div>
        """, unsafe_allow_html=True)
        
        # 1. 取得目前選取設施的「最終實體資料夾名稱」
        pure_id = facility_id
        if "(" in facility_id and ")" in facility_id:
            pure_id = facility_id.split("(")[-1].split(")")[0]
        pure_id = pure_id.strip()

        # 2. 🎯 核心聯動
        associated_w = str(target_row['_filter_w_series']).strip()
        system_folder_name = f"{associated_w}系統"
        
        # 3. 三層絕對路徑無痕對接
        basin_dir = os.path.join(PHOTO_BASE_DIR, facility_basin)
        target_folder_path = os.path.join(basin_dir, system_folder_name, pure_id)
        
        img_list = []
        if os.path.exists(target_folder_path) and os.path.isdir(target_folder_path):
            valid_extensions = ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG"]
            all_files = []
            for ext in valid_extensions:
                search_pattern = os.path.join(target_folder_path, "**", f"*.{ext}")
                all_files.extend(glob.glob(search_pattern, recursive=True))
            img_list = all_files
            img_list = sorted(list(set(img_list)))
            
        else:
            if os.path.exists(basin_dir) and os.path.isdir(basin_dir):
                valid_extensions = ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG"]
                all_files = []
                for ext in valid_extensions:
                    search_pattern = os.path.join(basin_dir, "**", f"*.{ext}")
                    all_files.extend(glob.glob(search_pattern, recursive=True))
                
                pure_id_upper = pure_id.upper()
                for file_path in all_files:
                    file_name = os.path.basename(file_path).upper()
                    if file_name.startswith(pure_id_upper):
                        remainder = file_name[len(pure_id_upper):]
                        if remainder == "" or not remainder[0].isalnum():
                            img_list.append(file_path)
                img_list = sorted(list(set(img_list)))

        if "remote_action" not in st.session_state:
            st.session_state["remote_action"] = None

        components.html("""
        <script>
            window.parent.addEventListener('message', function(e) {
                if (e.data && e.data.type === 'KEY_NAV') {
                    const btnId = e.data.direction === 'NEXT' ? 'remote_next_trigger' : 'remote_prev_trigger';
                    window.parent.document.getElementById(btnId)?.click();
                }
            });
        </script>
        """, height=0, width=0)

        if img_list:
            state_key = f"img_idx_{facility_id}"
            if state_key not in st.session_state:
                st.session_state[state_key] = 0
                
            btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 6])
            with btn_col1:
                prev_click = st.button("⬅️ 上一張", key=f"prev_{facility_id}")
                st.markdown(f"<button id='remote_prev_trigger' style='display:none;' onclick='document.getElementById(\"prev_{facility_id}\").click()'></button>", unsafe_allow_html=True)
                if prev_click:
                    st.session_state[state_key] = (st.session_state[state_key] - 1) % len(img_list)
                    st.rerun()
            with btn_col2:
                next_click = st.button("下一張 ➡️", key=f"next_{facility_id}")
                st.markdown(f"<button id='remote_next_trigger' style='display:none;' onclick='document.getElementById(\"next_{facility_id}\").click()'></button>", unsafe_allow_html=True)
                if next_click:
                    st.session_state[state_key] = (st.session_state[state_key] + 1) % len(img_list)
                    st.rerun()
            with btn_col3:
                st.markdown("<div style='text-align:right;' id='ext-fs-button-placeholder'></div>", unsafe_allow_html=True)

            current_idx = st.session_state[state_key]
            if current_idx >= len(img_list):
                current_idx = 0
                st.session_state[state_key] = 0
                
            file_name = os.path.basename(img_list[current_idx])
            try:
                with open(img_list[current_idx], "rb") as f:
                    encoded_img = base64.b64encode(f.read()).decode()
                ext_name = os.path.splitext(file_name)[1].lower().replace('.', '')
                mime_type = "image/png" if ext_name == "png" else "image/jpeg"
                img_data_src = f"data:{mime_type};base64,{encoded_img}"
                
                icon_svg = """
                <svg viewBox="0 0 24 24" width="20" height="20" fill="#fff">
                    <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/>
                </svg>
                """

                panzoom_raw_template = """
                <style>
                    #img-zoom-container:fullscreen {
                        width: 100vw !important; height: 100vh !important;
                        background-color: #000000 !important;
                        display: flex !important; align-items: center !important; justify-content: center !important;
                    }
                    #img-zoom-container:fullscreen #zoomable-img { max-width: 100% !important; max-height: 100% !important; }
                    #img-zoom-container:fullscreen #internal-info-bar { display: none !important; }
                    #pan-zoom-fullscreen-btn {
                        background-color: #333 !important; border: 1px solid #ccc !important;
                        border-radius: 50% !important; padding: 10px !important;
                        display: inline-flex !important; align-items: center !important; justify-content: center !important;
                        cursor: pointer !important; transition: background-color 0.15s ease !important;
                    }
                    #pan-zoom-fullscreen-btn:hover { background-color: #555 !important; }
                </style>
                
                <div id="img-zoom-container" style="width:100%; height:420px; overflow:hidden; background-color:#f5f5f5; border:1px solid #ddd; border-radius:8px; display:flex; align-items:center; justify-content:center; cursor:grab; position:relative; user-select:none;">
                    <img id="zoomable-img" src="__IMG_SRC__" style="max-width:100%; max-height:100%; transform-origin: center center; transition: transform 0.05s ease-out; transform: translate(0px, 0px) scale(1);">
                    <button id="pan-zoom-fullscreen-btn" style="display:none;">__ICON_SVG__</button>
                </div>

                <script>
                    const container = document.getElementById('img-zoom-container');
                    const img = document.getElementById('zoomable-img');
                    const fsIconBtn = document.getElementById('pan-zoom-fullscreen-btn');
                    const placeholder = window.parent.document.getElementById('ext-fs-button-placeholder');

                    let scale = 1, isDragging = false, startX, startY, translateX = 0, translateY = 0;

                    container.addEventListener('wheel', function(e) {
                        e.preventDefault();
                        const delta = e.deltaY < 0 ? 0.2 : -0.2;
                        scale = Math.min(Math.max(1.0, scale + delta), 10.0);
                        img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
                    });

                    container.addEventListener('mousedown', function(e) {
                        if (e.target === fsIconBtn || e.target.closest('#pan-zoom-fullscreen-btn')) return;
                        isDragging = true; container.style.cursor = 'grabbing';
                        startX = e.clientX - translateX; startY = e.clientY - translateY;
                    });

                    window.addEventListener('mousemove', function(e) {
                        if (!isDragging) return;
                        if (e.buttons !== 1) { isDragging = false; container.style.cursor = 'grab'; return; }
                        translateX = e.clientX - startX; translateY = e.clientY - startY;
                        img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
                    });

                    window.addEventListener('mouseup', function() { isDragging = false; container.style.cursor = 'grab'; });
                    container.addEventListener('mouseleave', function() { isDragging = false; container.style.cursor = 'grab'; });

                    window.addEventListener('keydown', function(e) {
                        if (document.fullscreenElement) {
                            if (e.key === 'ArrowRight') window.parent.postMessage({ type: 'KEY_NAV', direction: 'NEXT' }, '*');
                            else if (e.key === 'ArrowLeft') window.parent.postMessage({ type: 'KEY_NAV', direction: 'PREV' }, '*');
                        }
                    });

                    if (placeholder) {
                        fsIconBtn.id = 're-fs-btn'; fsIconBtn.style.display = 'inline-flex';
                        placeholder.innerHTML = ''; placeholder.appendChild(fsIconBtn);
                    }

                    function toggleFullscreen() {
                        if (!document.fullscreenElement) container.requestFullscreen().catch(() => {});
                        else document.exitFullscreen();
                    }

                    document.getElementById('re-fs-btn')?.addEventListener('click', toggleFullscreen);
                    fsIconBtn.addEventListener('click', toggleFullscreen);

                    document.addEventListener('fullscreenchange', function() {
                        if (!document.fullscreenElement) {
                            scale = 1; translateX = 0; translateY = 0; img.style.transform = `translate(0px, 0px) scale(1)`;
                        }
                    });
                </script>
                """
                panzoom_html = panzoom_raw_template.replace("__IMG_SRC__", img_data_src).replace("__ICON_SVG__", icon_svg)
                st.components.v1.html(panzoom_html, height=435)
            except Exception as img_err: st.error(f"圖片加載失敗: {img_err}")
        else:
            st.info(f"💡 在硬碟中找不到該設施的指定目標子資料夾：`/{facility_basin}/{system_folder_name}/{pure_id}/`")
    else:
        st.markdown(
            "<div style='border: 2px dashed #ccc; padding: 40px; text-align: center; color: #888; border-radius: 10px; margin-top: 20px;'>"
            "<h3>📋 尚未選取設施</h3><p>請在上方地圖點選圖標，或是點選左側表格列，系統將在此即時生成專屬詳細報告圖面。</p>"
            "</div>", unsafe_allow_html=True
        )

# =========================================================================
# 🌟 滿版詳細資料區（移至 5:5 欄位最下方，並自動處理多點缺失換行一對一排開）
# =========================================================================
if st.session_state["last_selected_index"] is not None and st.session_state["last_selected_index"] < len(df_filtered):
    idx = st.session_state["last_selected_index"]
    target_row = df_filtered.iloc[idx]
    
    st.markdown("---") # 加上視覺分隔線
    
    # 1. 內部統計與備註欄位
    st.markdown("### 📊 內部統計與備註")
    n2_col1, n2_col2 = st.columns(2)
    with n2_col1:
        raw_sub_pipe = target_row.get('分接管(內部統計)', '')
        sub_pipe_val = "—" if pd.isna(raw_sub_pipe) or str(raw_sub_pipe).strip() == "" or str(raw_sub_pipe).strip() == "無資料" else str(raw_sub_pipe)
        st.markdown(f"""
            <div style="padding: 5px 0;">
                <span style="color: #888; font-size: 1.1rem; font-weight: bold; display: block; margin-bottom: 8px;">📊 分接管數 (內部統計)：</span>
                <div style="font-size: 1.25rem; font-weight: bold; color: #333; background-color: #f9f9f9; padding: 10px; border-radius: 5px; border-left: 4px solid #2e7d32; min-height: 48px; line-height: 28px;">
                    {sub_pipe_val}
                </div>
            </div>
        """, unsafe_allow_html=True)
        
    with n2_col2:
        raw_note = target_row.get('備註', '')
        note_val = "—" if pd.isna(raw_note) or str(raw_note).strip() == "" or str(raw_note).strip() == "無備註資訊" else str(raw_note)
        st.markdown(f"""
            <div style="padding: 5px 0;">
                <span style="color: #888; font-size: 1.1rem; font-weight: bold; display: block; margin-bottom: 8px;">📝 管理備註說明：</span>
                <div style="font-size: 1.25rem; font-weight: 500; color: #333; background-color: #f9f9f9; padding: 10px; border-radius: 5px; border-left: 4px solid #757575; min-height: 48px; line-height: 28px;">
                    {note_val}
                </div>
            </div>
        """, unsafe_allow_html=True)
        
    st.markdown("<div style='margin-top:20px;'></div>", unsafe_allow_html=True)
    
    # 2. 🔄 核心排版優化：現況樣態說明 與 可優化改善方向
    st.markdown("### 🔄 現況與可優化方向")
    
    # 抓取原始字串並清理過濾
    raw_status = target_row.get('現況樣態', '')
    raw_optimize = target_row.get('可優化方向', '')
    
    status_str = "現場狀態正常或暫無登錄缺失" if pd.isna(raw_status) or str(raw_status).strip() == "" else str(raw_status).strip()
    optimize_str = "暫無優化建議" if pd.isna(raw_optimize) or str(raw_optimize).strip() == "" else str(raw_optimize).strip()
    
    # 核心切割技術：根據換行符號 \n 切割成獨立的點位清單
    status_lines = [line.strip() for line in status_str.split('\n') if line.strip()]
    optimize_lines = [line.strip() for line in optimize_str.split('\n') if line.strip()]
    
    # 計算最大行數，確保兩邊能「一對一」成雙成對往下排
    max_lines = max(len(status_lines), len(optimize_lines))
    
    # 先渲染上方大標題（模擬你的 error/success 紅綠區塊效果）
    title_col1, title_col2 = st.columns(2)
    with title_col1:
        st.error("⚠️ 現況樣態說明")
    with title_col2:
        st.success("💡 可優化改善方向")
        
    # 用迴圈一筆一筆排出來，只要有第 2 點，就絕對會自己「往下開新行」排列
    for i in range(max_lines):
        s_text = status_lines[i] if i < len(status_lines) else ""
        o_text = optimize_lines[i] if i < len(optimize_lines) else ""
        
        row_col1, row_col2 = st.columns(2)
        with row_col1:
            if s_text:
                st.markdown(f"""
                    <div style="background-color: #ffebee; padding: 12px; border-radius: 6px; margin-bottom: 8px; border-left: 4px solid #ef5350; font-size: 1.1rem; color: #c62828; min-height: 50px;">
                        {s_text}
                    </div>
                """, unsafe_allow_html=True)
            else:
                st.write("") # 空白占位保持平行
                
        with row_col2:
            if o_text:
                st.markdown(f"""
                    <div style="background-color: #e8f5e9; padding: 12px; border-radius: 6px; margin-bottom: 8px; border-left: 4px solid #66bb6a; font-size: 1.1rem; color: #2e7d32; min-height: 50px;">
                        {o_text}
                    </div>
                """, unsafe_allow_html=True)
            else:
                st.write("") # 空白占位保持平行
